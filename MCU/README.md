# MCU — STM32G474 BMS 펌웨어

STM32G474RET6 펌웨어입니다. BQ76922 BMIC로 4S 배터리 팩을 100 ms마다 측정하고, 이더넷(W5500)으로 Raspberry Pi와 데이터를 주고받습니다.

- **측정 전송**: `START` 이후 셀 전압·온도·전류를 Pi로 보냅니다.
- **출력 제어**: Pi 명령으로 모터와 충전을 켜고 끕니다. 둘은 동시에 켤 수 없습니다.
- **임계값 판정·차단**: Pi가 보낸 시뮬레이션 측정값(`SIM`)을 MCU가 직접 판정하고, 폴트면 모터·충전·FET를 끕니다.
- **실제 보호**: BMIC Safety/PF, ESTOP, 센싱 오류·최신성, 모터 드라이버 오류를 감시합니다. 시뮬레이션 값은 실제 측정값과 섞지 않습니다.

## 목차
1. [폴더 구성](#1-폴더-구성)
2. [코드 설명](#2-코드-설명)
3. [빌드 · 업로드](#3-빌드--업로드)
4. [통신 (Pi ↔ MCU)](#4-통신-pi--mcu)
5. [임계값 판정](#5-임계값-판정)
6. [검증 범위](#6-검증-범위)

---

## 1. 폴더 구성

```
MCU/
├── Core/Src/                 main.c · bq76922.c · bms_app.c · bms_demo.c · ethernet_test.c
├── Core/Inc/                 헤더
├── Drivers/                  ST HAL · CMSIS
├── aibms_1028_baseline.ioc   CubeMX 설정
├── Makefile                  독립 ARM 빌드
└── STM32G474RETX_*.ld        링커 스크립트
```

## 2. 코드 설명

| 파일 | 역할 |
|---|---|
| `main.c` | 초기화, 메인 루프 (`BMS_App_Update`, `Ethernet_Update`) |
| `bq76922.c` | BQ76922 I²C 드라이버: 측정, FET, 밸런싱 |
| `bms_app.c` | 100 ms 측정, 출력 허용 판단(`SensingAllowed`), 모터·충전·FET·밸런싱 제어 |
| `bms_demo.c` | `SIM` 수신과 임계값 판정 (`DemoJudge`), 폴트 래치 |
| `ethernet_test.c` | W5500 UDP: 측정 송신, 명령 수신, JSON 응답 |

듀티는 모터1 90 %, 모터2 30 %, PWM 20 kHz로 고정입니다.

## 3. 빌드 · 업로드

STM32CubeIDE에서 프로젝트를 불러오거나, Arm GNU Toolchain 13.2.rel1로 빌드합니다.

```bash
make CONFIG=Release TOOLCHAIN_BIN=/절대경로/arm-gnu-toolchain/bin
st-flash --connect-under-reset --reset write firmware/Release/aibms_1028_baseline.bin 0x08000000
```

## 4. 통신 (Pi ↔ MCU)

| 항목 | 값 |
|---|---|
| MCU / Pi | `192.168.50.10` / `192.168.50.2` (다른 IP의 패킷은 무시) |
| 측정 | MCU UDP 5000 → Pi UDP 5001 (`START` 후 100 ms 주기) |
| 명령 | Pi → MCU UDP 5002. 응답 JSON은 요청의 출발 포트로 돌아옴 |

측정 형식: `seq,ms,valid,V1,V2,V3,V4,T1,T2,T3,T4,I,STACK,PACK,LD` (읽기 실패 시 `valid=0`, 나머지 `NA`)

명령은 `MC1 <id> <명령>` 형식이고, **한 번에 하나만** 보냅니다. MCU의 응답 칸이 1개라 겹치면 앞 응답이 사라집니다.

| 명령 | 동작 |
|---|---|
| `STATUS` | 상태 조회 |
| `START` / `STOP` | 센싱·측정 송신 시작 / 전체 출력 OFF 후 정지 |
| `MOTOR ON <rev>` / `MOTOR OFF` | 모터 ON / OFF (OFF 후 센싱 유지) |
| `CHARGE ON <rev>` / `CHARGE OFF` | 충전 ON / OFF (OFF 후 센싱 유지) |
| `FET ON <rev>` / `FET OFF` / `OFF` | FET ON / OFF, 전체 출력 OFF |
| `SIM seq,ms,emergency,V1..V4,T1..T4,I,STACK,PACK,LD` | 시뮬레이션 측정값. `emergency` 2 = MCU 판정, 0 / 1 = 시험용 강제 폴트 / 정상 |
| `SIM CLEAR` | 폴트 해제. 출력은 자동으로 켜지지 않음 |

ON의 `rev`는 `STATUS`에서 읽은 값을 씁니다. ON 응답 `rc=0`은 요청 수락이므로, 실제 적용은 `STATUS`의 `stage`(3 = 모터 PWM), `charge_on`으로 확인합니다.

주요 `rc`: `-11` 센싱, `-12` ESTOP, `-13` BMIC 보호, `-24` 모터·충전 동시 사용, `-27` 모터 드라이버, `-30` 오래된 rev, `-40` START 전, `-43` SIM 순서 오류, `-44` 시연 폴트 유지 중.

## 5. 임계값 판정

`SIM`(`emergency=2`) 값에 적용합니다. 표본이 끊기면 연속 횟수를 다시 셉니다.

| 항목 | 조건 | 연속 표본 (10 Hz) | `sim_bits` |
|---|---|---|---|
| COV | 셀 > 4.25 V | 5 (0.5 s) | 1 |
| CUV | 셀 < 2.80 V | 10 (1 s) | 2 |
| OCD | 방전 > 6 A | 1 (0.1 s) | 4 |
| OT | 온도 > 60 °C | 10 (1 s) | 8 |

폴트가 나면 `SensingAllowed()`가 `-44`를 돌려줘 모터 PWM, 충전, BMIC FET를 끄고, `SIM CLEAR` 전까지 ON을 거부합니다. 센싱과 측정 송신은 계속됩니다. 상태는 `STATUS`의 `algo`, `sim_fault`, `sim_bits`, `first_fault_ms`로 확인합니다.

## 6. 검증 범위

- **ARM 빌드:** Release 전체 컴파일·링크, `-Wall -Wextra -Werror` 통과
- **실제 보드:** 플래시, 측정 수신, 모터 ON/OFF, 충전 ON/OFF, `SIM` 임계값 판정, 래치와 해제
- **모의 HAL과 모의 MCU:** 폴트 시 출력 차단과 `-44` 재기동 거부
- **미확인:** 실제 모터·충전이 도는 중 폴트로 차단되는 동작, 충전 조건 경계
