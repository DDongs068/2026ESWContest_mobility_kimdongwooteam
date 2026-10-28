# MCU — STM32G474 BMS 펌웨어

STM32G474RET6 펌웨어입니다. BQ76922 BMIC로 4S 배터리 팩을 100 ms마다 측정하고, 이더넷(W5500)으로 Raspberry Pi와 데이터를 주고받습니다.

- **측정 전송**: 셀 전압·온도·전류를 Pi로 보냅니다.
- **AI 판정 반영**: Pi의 AE가 이상을 판정하면 MCU가 수신 즉시 보호 래치를 걸고 출력 전체를 차단합니다.
- **안전 감시**: BMIC의 Safety/PF 상태, 통신, 측정 신선도, ESTOP을 따로 감시해 위험하면 기동을 막습니다.
- **측정 재생**: 시뮬레이션 측정값을 PCB를 거쳐 재생합니다. 재생값은 실측 보호 판단과 섞지 않습니다.

## 목차
1. [하드웨어](#1-하드웨어)
2. [폴더 구성](#2-폴더-구성)
3. [코드 설명](#3-코드-설명)
4. [빌드 · 업로드](#4-빌드--업로드)
5. [통신 (Pi ↔ MCU)](#5-통신-pi--mcu)
6. [안전 동작](#6-안전-동작)
7. [검증 범위](#7-검증-범위)

---

## 1. 하드웨어

| 구성 | 부품 | 연결 |
|---|---|---|
| MCU | STM32G474RET6 (Cortex-M4F) | — |
| BMIC | TI BQ76922 (4S, 셀 전압·온도·전류, 하드웨어 보호, 셀 밸런싱) | I²C1 (7-bit 주소 0x08) |
| 이더넷 | WIZnet W5500 | SPI |
| 충전 IC | TI BQ24610 | CHG_CMD (PA2), 상태 핀 |
| 부하 | DC-DC + 모터 드라이버 2채널 (20 kHz PWM) | DCDC_CMD (PB14), MOTOR_CMD (PB13) |
| 배터리 | 4S1P (Samsung 35E) | BMIC 채널 1, 2, 3, 5 |

## 2. 폴더 구성

```
MCU/
├── Core/
│   ├── Src/                       직접 작성한 펌웨어 (아래 코드 설명)
│   │   ├── main.c                 초기화 + 메인 루프
│   │   ├── bq76922.c              BQ76922 드라이버 (I²C)
│   │   ├── bms_app.c              측정 · 부하 / 충전 / 밸런싱 제어
│   │   ├── safety_supervisor.c    독립 안전 감시 · 보호 래치
│   │   ├── ethernet_test.c        W5500 UDP: 측정 전송 · AI 판정 · 원격 명령
│   │   ├── demo_stream.c          시뮬레이션 측정 프레임 재생
│   │   └── stm32g4xx_it.c, stm32g4xx_hal_msp.c, system_stm32g4xx.c, syscalls.c, sysmem.c   (CubeMX 생성)
│   ├── Inc/                       헤더 (각 .c 와 같은 이름 + main.h · stm32g4xx_hal_conf.h)
│   └── Startup/startup_stm32g474retx.s
├── Drivers/                       ST HAL · CMSIS (ST 라이선스)
├── aibms_true_final_mcu.ioc       STM32CubeMX 핀 · 클럭 설정
├── .project · .cproject · .mxproject · .settings/   STM32CubeIDE 프로젝트
├── STM32G474RETX_FLASH.ld · STM32G474RETX_RAM.ld    링커 스크립트
└── Makefile                       CubeIDE 없이 ARM GCC 로 빌드
```

## 3. 코드 설명

### 메인 루프 (`main.c`)

```c
HAL_Init();  MX_GPIO_Init();  SystemClock_Config();  MX_I2C1_Init();
BMS_App_Init(&hi2c1);           // BQ76922 설정 · 부하 OFF 로 시작
Ethernet_Test_Init();           // W5500 네트워크 설정
Safety_Init();  Safety_RefreshProfile();   // 보호 설정 읽기 전용 점검
while (1) {
    Safety_Update();            // 안전 감시
    Demo_Update();              // 재생 프레임 진행
    Safety_Update();            // 재생 이상을 BMS / I²C 처리 전에 차단
    BMS_App_Update();           // 100 ms 측정 · 출력 제어
    Safety_Update();            // 안전 감시 (측정 후)
    Ethernet_Test_Update();     // 측정 전송 · 명령 / AI 판정 수신
    Safety_Update();            // 이번 루프에 받은 명령 · AI 경보 반영
    HAL_Delay(5);
}
```

### 모듈별 역할

| 파일 | 하는 일 | 주요 함수 · 상태 |
|---|---|---|
| `bq76922.c/.h` | BQ76922 드라이버<br>측정값을 읽고, FET를 켜고 끄고, 밸런싱 명령을 보내고, 보호 설정을 읽기 전용으로 점검합니다.<br>I²C 제한 시간은 25 ms입니다. | `BQ_Init` · `BQ_Configure`: 4셀 매핑, 측정 단위, 10 mΩ 전류 스케일, NTC 설정<br>`BQ_Read`: 셀 전압 4, STACK/PACK/LD, 전류(충전 +), 온도 4, Safety/PF 상태<br>`BQ_EnableFETs` · `BQ_DisableFETs`<br>`BQ_BalanceSet`<br>`BQ_ReadProtectionProfile`: 보호 설정을 절대 쓰지 않음 |
| `bms_app.c/.h` | BMS 동작 본체<br>100 ms마다 측정하고, 정해진 순서대로 부하를 켜고 끕니다.<br>(FET → BMS_ALLOW → DC-DC → 모터)<br>충전 허가와 자동 셀 밸런싱도 맡고, AI 경보가 오면 전체를 OFF합니다. | `BMS_App_Update`<br>`g_bms`: 측정값, 단계, 결과 코드<br>`g_control`: ON/OFF 요청, 모터 듀티(기본 90 %), `ai_alarm`<br>`g_balance` · `g_charge` |
| `safety_supervisor.c/.h` | 독립 안전 감시 (실측값만 사용)<br>다음 조건 중 하나라도 걸리면 출력을 차단하고 래치합니다.<br>BMIC Safety/PF, 통신 실패, 500 ms 넘은 측정값, ESTOP, 센서 이상, DSG FET 미도통, 보호 설정 누락, AI 경보, 재생 데이터 이상, BMIC FET 명령 실패 | `Safety_Update`: 메인 루프에서 단계마다 호출<br>`Safety_Trip`: 즉시 래치 + GPIO/PWM 먼저 OFF<br>`Safety_CanEnable`: ON · 밸런싱 요청 전 기동 가능 여부 확인<br>`Safety_Rearm`: 원인이 사라진 뒤 명시적 ACK로만 해제<br>`g_safety.active / latched`: 비트 의미는 [6. 안전 동작](#6-안전-동작) |
| `ethernet_test.c/.h` | W5500 UDP 통신<br>· 100 ms마다 측정 CSV를 Pi로 보냄<br>· Pi의 AI 판정(`AI:1` / `AI:0`)을 받음<br>· `MC1` 원격 명령을 처리하고 JSON ACK로 응답 | `Ethernet_Test_Update`<br>`Ethernet_Test_Encode`: BMS2 CSV<br>`g_eth_control`: IP, 포트, 전송 ON/OFF<br>`g_eth`: 송수신 통계 |
| `demo_stream.c/.h` | 시뮬레이션 측정 재생<br>72바이트 프레임을 검사하고(CRC32, 세션, 순서, 100 ms 간격, 값 범위) 16개 큐로 재생합니다.<br>재생값은 `g_bms.sensor`(실측)를 덮어쓰지 않습니다.<br>재생 데이터에는 비교용 기준(COV 4.25 V / CUV 2.80 V / 방전 6 A / OT 60 °C)과 과충전 규칙(최근 10표본 = 1 s의 셀 최대 전압 중앙값 > 4.205 V, 10표본마다 계산)을 따로 기록합니다. | `Demo_Start` · `Demo_Stop`<br>`Demo_ReceiveHex`<br>`Demo_Sample`<br>`g_demo` |
| `stm32g4xx_*.c` · `system_*` · `sys*.c` | CubeMX가 만든 인터럽트, HAL MSP, 시스템 초기화, C 런타임 | — |

## 4. 빌드 · 업로드

**STM32CubeIDE (권장)**
1. `File → Import → Existing Projects into Workspace`에서 `MCU/` 폴더를 선택합니다.
2. `aibms_true_final_mcu` 프로젝트를 **Debug**로 빌드하면 `Debug/aibms_true_final_mcu.elf`가 나옵니다.
3. ST-LINK를 연결하고 `Run` 또는 `Debug`로 업로드합니다.

> CubeMX로 코드를 다시 생성하면 `main.c`의 직접 작성한 부분이 덮어써질 수 있습니다. 이 저장소의 소스를 기준으로 빌드하세요.

**Makefile (ARM GCC)**
```bash
cd MCU
make TOOLCHAIN_BIN=/path/to/arm-gnu-toolchain/bin               # Debug
make CONFIG=Release TOOLCHAIN_BIN=/path/to/arm-gnu-toolchain/bin
```
- 출력은 `MCU/<CONFIG>/aibms_true_final_mcu.{elf,hex,bin,map}`이고, 중간 파일은 `MCU/build/<CONFIG>/`에 생깁니다.

## 5. 통신 (Pi ↔ MCU)

| 항목 | 값 |
|---|---|
| MCU / Pi IP | 192.168.10.20 / 192.168.10.1 (255.255.255.0) |
| 측정 전송 | MCU → Pi **UDP 5001**, 100 ms 주기 (부팅 후 기본 ON, `STREAM OFF`로 정지) |
| AI 판정 | Pi → MCU **UDP 5000**: `AI:1` = 수신 즉시 보호 래치 + 출력 전체 OFF, `AI:0` = AI 입력만 정상으로 바꿈 (래치 유지, 자동 재기동 없음) |
| 원격 명령 | Pi → MCU **UDP 5002**: `MC1 <id> <명령>`, 보낸 포트로 JSON ACK 회신 |

- 지정한 Pi IP에서 온 명령만 받습니다.
- 인증이나 암호화가 없는 관리된 로컬망을 전제로 합니다.

**측정 CSV (BMS2, 28개 필드, 한 줄)**
```
BMS2,source,session,sequence,ms,valid,V1,V2,V3,V4,T1,T2,T3,T4,I,STACK,PACK,LD,B1,B2,B3,B4,sim_baseline,sim_overcharge,live_active,live_latched,ai_alarm,live_fet
```
- `source`: 0 = 실측, 1 = 재생
- 단위: V는 V, T는 °C, I는 A(충전 +, 방전 −), B는 셀 밸런싱 0/1
- 유효하지 않은 측정값은 `NA`로 보냅니다.

**원격 명령**
```
MC1 <id> STATUS                         상태 조회 (rev 포함)
MC1 <id> ON <rev> / OFF                 부하 ON (최신 rev 필요) / 전체 OFF
MC1 <id> ACK                            보호 래치 해제 (원인 해소 + 출력 · 밸런싱 · PWM 모두 OFF 일 때만)
MC1 <id> STREAM ON|OFF                  측정 주기 전송
MC1 <id> BALANCE ON|OFF                 자동 셀 밸런싱
MC1 <id> REPLAY START <session> / STOP  재생 시작 / 종료
MC1 <id> REPLAY FRAME <144자리 hex>      재생 프레임 1개 (72 바이트)
MC1 <id> RESULT <session> <seq> <0|1>   재생 프레임에 대한 AI 판정 (1 = 보호 래치 + 전체 OFF)
```

반환 코드는 다음과 같습니다.

| 코드 | 의미 |
|---|---|
| −30 | 오래된 ON 요청 |
| −40 | 형식 / CRC 오류 |
| −41 | 세션 오류 |
| −42 | 큐 포화 |
| −43 | 순서 / 간격 / 범위 오류 |
| −44 | AI 결과 세션 / 프레임 오류 |
| −45 | 재생 입력 1 s 유실 |
| −50 | 안전 원인 · 래치 · 출력 ON 상태 때문에 기동 / ACK 불가 |

**재생 프레임 (72 바이트, big-endian)**

| 오프셋 | 필드 |
|---|---|
| 0–3 | `BDP1` |
| 4–7 · 8–11 · 12–15 | session · sequence · 원본 ms (100 ms 간격) |
| 16–31 | V1~V4 [mV] |
| 32–47 | T1~T4 [m°C] |
| 48–51 | 팩 전류 [mA] (충전 +) |
| 52–63 | STACK / PACK / LD [mV] |
| 64–67 | 셀 밸런싱 마스크 |
| 68–71 | CRC32 (IEEE, 앞 68 바이트) |

## 6. 안전 동작

`g_safety.active` / `latched` 비트:

| 비트 | 원인 | 비트 | 원인 |
|---|---|---|---|
| 1 | BMIC Safety / PF | 32 | 모터 드라이버 nFAULT |
| 2 | BMIC 통신 실패 | 64 | DSG FET 미도통 (기동 후 300 ms 유예) |
| 4 | 500 ms 넘은 측정값 / 미측정 | 128 | BMIC 설정 오류 |
| 8 | ESTOP | 256 | 보호 설정 미확인 / 필수 보호 비활성 |
| 16 | 센서 유효성 | 512 | AI 경보 (`AI:1` / `RESULT 1`) |
| 1024 | 재생 데이터 기준 초과 · 과충전 규칙 · 재생 유실 | 2048 | BMIC FET ON/OFF 명령 실패 |

- **필수 보호 확인**: 부팅할 때와 5 s마다 BMIC 보호 설정을 **읽기만** 해서 확인합니다.
  - 보호 enable: SCD / OCD1 / OCC / COV / CUV, OTD / OTC / UTD / UTC
  - CHG / DSG FET 매핑
  - 하나라도 빠졌으면 기동을 막습니다. 보호 설정을 자동으로 쓰거나 끄지 않습니다.
- **즉시 차단**: AI 경보와 재생 데이터 이상은 출력이 꺼져 있어도 바로 래치합니다. 래치되면 GPIO/PWM을 먼저 내리고, 그 다음 BMIC OFF 명령을 보냅니다.
- **해제 순서**: 원인 해소 → 출력 · 밸런싱 · 모터 PWM 전체 OFF 상태에서 `MC1 <id> ACK` → 새 ON 요청
  - 재생 이상이 원인이면 먼저 `REPLAY STOP`으로 시험을 끝내야 ACK가 됩니다.
  - FET OFF 실패, 남은 PWM, 진행 중인 밸런싱이 있으면 ACK를 거부합니다(−50).
  - ACK가 성공하면 그 전에 쌓인 ON · 충전 · 밸런싱 요청은 취소됩니다.
  - ACK나 `AI:0`만으로는 모터가 다시 켜지지 않습니다.

## 7. 검증 범위

- **확인한 것**: 팀에서 ARM 빌드와 호스트 모델 시험을 했습니다.
  - BQ 드라이버 checksum, 보호 설정 점검, Safety/PF 56비트
  - 출력 차단과 재기동 금지
  - CRC, 순서, 중복, 포화, 재생 유실
  - 1 s(10표본) 과충전 경계
- **실물 검증 대기**: 보호 지연, 실제 차단 전류, 센싱 정확도, 부하 상태 타이밍, Pi와의 종단 통신은 아직 실물로 확인하지 않았습니다.
