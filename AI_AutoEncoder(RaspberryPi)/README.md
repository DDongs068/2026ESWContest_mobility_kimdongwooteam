# AI 파트 — AE (AutoEncoder)

정상 데이터만으로 학습한 AE로 배터리 팩의 위험 이상을 1 s마다 판정합니다. 이상이 나면 문제 셀을 지목합니다.

학습은 PC에서 하고(`1_preprocess` → `2_train` → `5_weights`), **판정은 Raspberry Pi 5가 합니다.** Pi는 이 폴더의 모델 코드(`3_model`)와 가중치(`5_weights/AE`)를 그대로 불러와 numpy만으로 실행합니다. Pi에서 도는 판정 서버와 통신 코드는 [`RaspberryPi/`](RaspberryPi)에 있습니다.

## 목차
1. [폴더 구성](#1-폴더-구성)
2. [준비](#2-준비)
3. [코드 설명과 사용법](#3-코드-설명과-사용법)
   - [1_preprocess — 전처리](#1_preprocess--전처리)
   - [2_train — 모델 학습](#2_train--모델-학습)
   - [3_model — 모델](#3_model--모델)
   - [4_eval — 평가](#4_eval--평가)
   - [5_weights — 모델 가중치](#5_weights--모델-가중치)
   - [RaspberryPi — Pi 실시간 판정](#raspberrypi--pi-실시간-판정)
4. [실시간 추론 예시 (Raspberry Pi)](#4-실시간-추론-예시-raspberry-pi)
5. [데이터 형식](#5-데이터-형식)

---

## 1. 폴더 구성

```
AI_AutoEncoder(RaspberryPi)/
├── 1_preprocess/preprocess.py   실측 CSV (10 Hz) → 1 s 윈도우 요약
├── 2_train/train.py             정상 데이터로 AE 학습 → 가중치 · 판정 경계
├── 3_model/
│   ├── model.py                 AE 구조 · 이상 점수 · 전문가 로드 · 과충전 규칙 (PyTorch)
│   ├── np_model.py              numpy 추론 (Pi 배포용) · .pt → .npz 변환
│   ├── features.py              55 s 이력 · 셀/팩 특징 · 정규화
│   └── router.py                운전 상태 판별 (학습 없음) · 상태 전환 시 이력 리셋
├── 4_eval/
│   ├── eval.py                  평가 (confusion matrix · 오경보 · 탐지 지연 · HW 보호 대비)
│   └── results/                 평가 결과
├── 5_weights/AE/                학습된 가중치 (충전 chg · 주행 dis · 휴지 rest) + threshold.json
├── RaspberryPi/                 Pi 5 에서 실행: AE 판정 서버 · 모니터 UI · 통신 코드 · 패널 대리인 · 자동 실행 서비스
└── data/split_blocks.csv        블록 단위 학습 / 검증 / 테스트 분할
```

## 2. 준비

```bash
pip install numpy pandas scipy torch matplotlib     # Pi 추론만 할 때는 numpy 만 있으면 됨
```

- 모든 명령은 `AI_AutoEncoder(RaspberryPi)/` 폴더에서 실행합니다.
- 경로는 각 코드 맨 위에 상수로 들어 있습니다. 설정 파일이나 인자는 쓰지 않습니다.
- **학습·평가용 원본 데이터는 저장소에 없습니다.**
  - 실측 정상 CSV를 `raw/normal_windows.csv`에 둡니다.
  - 비정상 시뮬레이션 데이터(`abnormal_index.csv`, `csv/`)를 `raw/abnormal/`에 둡니다.
  - 제공된 가중치(`5_weights/AE`)로 추론하는 것은 원본 데이터 없이 바로 됩니다.

---

## 3. 코드 설명과 사용법

### 1_preprocess — 전처리

**`preprocess.py`**: 10 Hz 실측 CSV를 모델 입력 단위인 **1 s 윈도우 요약**으로 바꿉니다.

| 단계 | 내용 |
|---|---|
| 윈도우 | 10샘플(1 s)을 평균합니다. |
| 요약 S (13) | 셀 전압 V̄1~4, 셀 온도 T̄1~4, 밸런싱 B̄1~4(항상 0), 팩 전류 Ī. 과충전 규칙용으로 윈도우 최대 셀 전압 중앙값도 남깁니다. |
| 연속 구간 | 파일이 바뀌거나 seq가 끊기거나 간격이 2 s를 넘으면 새 구간입니다. 이력은 구간을 넘지 않습니다. |
| 분할 | `data/split_blocks.csv`의 180 윈도우(약 3분) 블록 단위 train / val / test를 따릅니다. split이 바뀐 직후 55 윈도우는 이력이 다른 split을 포함하므로 쓰지 않습니다. |

```bash
python 1_preprocess/preprocess.py
```
- **입력**: `raw/normal_windows.csv` (`window_id, seq, ms, valid, V1..4, T1..4, I, STACK, PACK, LD`)
- **출력**: `data/summary_normal.npz` + `data/meta_normal.csv`
- 사용 윈도우: train 36,014 · val 6,400 · test 12,053

### 2_train — 모델 학습

**`train.py`**: 운전 상태(충전 / 주행 / 휴지)마다 **셀 AE + 팩 AE**를 정상 데이터로만 학습합니다. 판정 경계 τ도 함께 정합니다.

| 항목 | 값 |
|---|---|
| 학습 데이터 | 실측 정상 train만 (이상 데이터 미사용) |
| 셀 AE | 셀 하나를 샘플 하나로 봅니다. 4개 셀이 같은 가중치를 공유해, 한 셀만 다르게 움직이면 그 셀의 오차가 커집니다. |
| 손실 | 복원오차 mean (ẑ − z)². 상태 라벨별로 균등하게 뽑아 학습합니다. |
| 최적화 | AdamW lr 1e-3, weight decay 1e-4, cosine 스케줄, 60 epoch, early stopping 15 |
| 판정 경계 | 충전·주행 τ = 정상 val p99.5 × 1.2, 휴지 τ = 정상 val 최댓값 × 1.2 (셀 / 팩 따로) |

```bash
python 2_train/train.py
```
- **출력**: `5_weights/AE_retrain/`
  - `<chg|dis|rest>/best_{cell,pack}_ae.pt`, `stats_{cell,pack}.json`, `history_*.csv`
  - `threshold.json`
- 제공 가중치(`5_weights/AE`)는 덮어쓰지 않습니다.
- Pi에서 쓰려면 학습 뒤 numpy 가중치로 변환합니다. 변환은 [np_model.py](#3_model--모델)를 참고하세요.

### 3_model — 모델

| 파일 | 하는 일 | 주요 함수 / 클래스 |
|---|---|---|
| `model.py` | AE 구조와 이상 점수 (PyTorch) | `FlowAE`: (C × 12) → 128 → 64 → **12** → 64 → 128 → (C × 12) MLP<br>`Expert(dir, device).scores(H)`: 셀별 점수 (N, 4), 팩 점수 (N,)<br>`spec_overcharge`: 최대 셀 > 4.205 V |
| `np_model.py` | 같은 추론을 **numpy만으로** 실행 (Pi용) | `NpExpert(dir).scores(H)`: `Expert`와 같은 결과 (오차 < 1e-4)<br>`export_weights(dir)`: `best_*_ae.pt` → `weights_*.npz` |
| `features.py` | 이력과 특징 계산 | `build_history`: 5 s 간격 윈도우 12개(55 s)<br>`cell_features`: 셀당 9개 (다른 셀 대비 전압·온도 차이, 55 s 전 대비 변화, 기울기, 전류·팩 평균)<br>`pack_features`: 10개 (전압·온도 평균 / 최대 / 편차 / 기울기, 전류)<br>`normalize`: 정상 통계로 정규화, ±20으로 자름 |
| `router.py` | 운전 상태 판별 (학습 없음) | `window_state`: 1 s 평균 전류 > +0.03 A이면 충전 `chg`, < −0.03 A이면 주행 `dis`, 그 사이면 휴지 `rest`<br>`routed_history`: 상태가 바뀌면 이력을 새로 시작<br>`route_of`: 현재 상태 → 사용할 전문가 |

각 파일을 직접 실행하면 간단한 자체 점검을 합니다.

```bash
python 3_model/features.py      # 특징 계산 확인
python 3_model/router.py        # 상태 판별 · 이력 리셋 확인
python 3_model/model.py         # AE 구조 · 과충전 규칙 확인
python 3_model/np_model.py      # .pt → .npz 변환 + torch 판과 점수 일치 확인 (전처리 결과 필요)
```

**판정 규칙**

```
이상도 = max( 셀 점수 최대 / τ_cell ,  팩 점수 / τ_pack )
이상   = 이상도 > 1   또는   최대 셀 전압 > 4.205 V
지목 셀 = 셀 점수가 가장 큰 셀
```

### 4_eval — 평가

**`eval.py`**: 제공 가중치로 정상 test와 비정상 시뮬레이션 test를 판정합니다.
- 정상: 학습에 쓰지 않은 실측 test 12,053 윈도우
- 이상: 실측에 Simscape 고장을 더한 test 118 파일의 `label=1` 윈도우 12,494개. Soft ISC 1~10 Ω(주행·휴지·충전), 과충전(CV 상실)

```bash
python 4_eval/eval.py
```

| 지표 | 의미 |
|---|---|
| confusion matrix · recall · precision · 오경보율 | 1 s 윈도우 단위 |
| 파일 탐지 · 고장 전 경보 | 파일별 탐지 여부, 고장 시작 전 구간의 경보 수 |
| 고장 후 첫 경보 | 고장 시작부터 첫 경보까지 걸린 시간 |
| 임계값 보호 대비 | COV, CUV, OCD, OT 보호가 동작한 파일과 AE가 먼저 경보한 수 |

- **출력**: `4_eval/results/results.txt`, `compare.csv`
- **현재 결과**: TN 11,962 · FP 91 · FN 219 · TP 12,275
  - recall 98.2 %, 오경보율 0.76 %, 파일 118/118
  - 오경보 91건 중 72건이 휴지 (휴지 오경보율 6.5 %)
  - 고장 시작 전 경보 0건

### 5_weights — 모델 가중치

```
5_weights/AE/
├── threshold.json                 경로별 판정 경계 τ (cell / pack) · 학습 설정
├── chg/  (충전 전문가)
├── dis/  (주행 전문가)
└── rest/ (휴지 전문가)
     ├── best_cell_ae.pt  best_pack_ae.pt      PyTorch 가중치 (학습 PC)
     ├── weights_cell.npz weights_pack.npz     numpy 가중치 (Pi)
     └── stats_cell.json  stats_pack.json      정규화 통계 (정상 train 의 median · scale)
```

| 경로 | τ_cell | τ_pack | τ 규칙 | 학습 윈도우 |
|---|---|---|---|---|
| 충전 `chg` | 1.424 | 0.887 | val p99.5 × 1.2 | 8,278 |
| 주행 `dis` | 1.706 | 0.513 | val p99.5 × 1.2 | 23,178 |
| 휴지 `rest` | 0.990 | 0.609 | val 최댓값 × 1.2 | 4,558 |

### RaspberryPi — Pi 실시간 판정

위에서 학습한 AE를 Raspberry Pi 5에서 실제로 실행하는 코드입니다. 자세한 내용은 [RaspberryPi/README.md](RaspberryPi/README.md)에 있습니다.

| 폴더 | 역할 |
|---|---|
| `RaspberryPi/monitor/` | `monitor_server.py`가 `3_model`과 `5_weights/AE`를 불러와 MCU 측정값을 1 s마다 판정하고, 결과를 모니터 UI(포트 8050)에 표시합니다. |
| `RaspberryPi/relay/` | MCU 측정 수신(5001), 시뮬레이션 데이터 수신(5013), 엑셀 버퍼, 모니터로 전달(5005) |
| `RaspberryPi/control_panel/` | Mac 컨트롤 패널의 Pi 쪽 대리인: 프레임을 AE 경로와 MCU로 분배 |
| `RaspberryPi/systemd/` | 부팅할 때 수신·버퍼 코드를 자동으로 실행하는 서비스 파일 |

```bash
bash RaspberryPi/monitor/run.sh        # AE 판정 서버 + UI → http://10.55.0.2:8050
```

---

## 4. 실시간 추론 예시 (Raspberry Pi)

`RaspberryPi/monitor/monitor_server.py`가 1 s마다 하는 일을 줄이면 아래와 같습니다. 최근 55 s 이력 `H`(5 s 간격 1 s 윈도우 12개)를 만들어 판정하며, numpy만 있으면 됩니다.

```python
import json, sys
import numpy as np
sys.path.insert(0, "3_model")
from np_model import NpExpert
from router import window_state

# H: (1, 12, 13) — 오래된 → 최신, 각 윈도우 = [V1..V4, T1..T4, B1..B4(=0), I]
H = np.zeros((1, 12, 13), dtype=np.float32)
H[0, :, 0:4] = 3.70           # 셀 전압 [V]
H[0, :, 4:8] = 25.0           # 셀 온도 [°C]
H[0, :, 12] = -0.8            # 팩 전류 [A] (충전 +, 방전 −)

route = window_state(H[:, -1])[0]                                         # chg / dis / rest
thr = json.load(open("5_weights/AE/threshold.json", encoding="utf-8"))["experts"][route]
cell, pack = NpExpert(f"5_weights/AE/{route}").scores(H)                  # (1, 4), (1,)
ratio = max(cell[0].max() / thr["cell"], pack[0] / thr["pack"])
print(route, "이상" if ratio > 1 else "정상", f"이상도 {ratio:.2f}", "셀", int(cell[0].argmax()) + 1)
# → dis 정상 이상도 0.37 셀 1
# 셀2 전압만 최근 30 s 동안 60 mV 처지게 하면 → dis 이상 이상도 4.79 셀 2
```

- 상태가 바뀌면 이력을 그 시점부터 새로 채웁니다. 모자란 칸은 구간의 첫 윈도우로 채웁니다(`router.routed_history`와 같은 규칙).
- 판정 1회에 Raspberry Pi 5 기준 약 0.65 ms가 걸립니다 (numpy, 1스레드).

## 5. 데이터 형식

**실측 정상 CSV** (`raw/normal_windows.csv`, 10 Hz, 10행 = 1윈도우): `window_id, seq, ms, valid, V1..4, T1..4, I, STACK, PACK, LD`
- 전류는 충전 +, 방전 −입니다.

**비정상 CSV** (`raw/abnormal/csv/`): 위 열 + `fault_on, label`
- 실측 val·test 블록에 고장과 정상 쌍둥이 시뮬레이션의 차이를 더한 값입니다.
- `fault_on = 1`: 고장 시작 이후. `label = 1`: 어느 셀이든 |ΔV| ≥ 20 mV 또는 |ΔT| ≥ 1 °C가 된 이후입니다.
- `fault_on = 1, label = 0` 구간은 평가에서 세지 않습니다.

**분할** (`data/split_blocks.csv`): `block, split` (180 윈도우 블록 362개: train 227 · val 50 · test 85)
