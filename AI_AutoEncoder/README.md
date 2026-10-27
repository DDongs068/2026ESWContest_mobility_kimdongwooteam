# AI (AutoEncoder)

정상 데이터만으로 학습한 AE가 배터리 팩의 위험 이상을 탐지합니다.

- 탐지 대상은 위험 soft ISC(내부단락)와 과충전입니다.
- 1 s마다 판정하고, 이상이 난 셀을 지목합니다.
- Raspberry Pi 5에서 numpy만으로 추론합니다(torch 불필요).

## 폴더

| 폴더 | 내용 |
|---|---|
| `1_preprocess/preprocess.py` | 원본 시뮬레이션 run(10 Hz)을 1 s 윈도우 요약으로 변환합니다. 정상 학습 데이터의 온도에는 실측 NTC 센서 특성을 반영합니다. |
| `2_train/train.py` | 정상 데이터로 AE를 학습합니다. 운전 상태(충전 / 주행 / 휴지)마다 셀 AE와 팩 AE를 따로 두고, 판정 경계 τ도 함께 정합니다. |
| `3_model/` | `model.py`: AE 구조, 점수, 전문가 로드, 과충전 규칙<br>`features.py`: 이력과 특징 계산<br>`router.py`: 학습 없는 센싱 라우터<br>`np_model.py`: Pi용 numpy 추론 |
| `4_eval/eval.py` | 평가합니다. confusion matrix, 오경보, ISC 탐지와 지연, 하드웨어 보호 대비 결과를 냅니다. 결과는 `4_eval/results/`에 저장됩니다. |
| `5_weights/AE/` | 학습된 가중치입니다. 전문가(`chg`·`dis`·`rest`)마다 `best_*_ae.pt`(torch), `weights_*.npz`(numpy), `stats_*.json`(정규화 통계)이 있습니다. 경계값은 `threshold.json`에 있습니다. |
| `data/split_runs.csv` | 학습·검증·테스트를 run 단위로 나눈 목록입니다. |

## 실행

```bash
pip install numpy pandas scipy torch matplotlib
python 1_preprocess/preprocess.py   # raw/normal, raw/fault3 의 원본 run 필요 (저장소 미포함, Simscape 디지털 트윈으로 생성)
python 2_train/train.py             # → 5_weights/AE_retrain/  (제공 가중치는 덮어쓰지 않음)
python 4_eval/eval.py               # 제공 가중치 5_weights/AE 로 평가
```

Pi 추론은 아래처럼 numpy만으로 할 수 있습니다.

```python
import sys; sys.path.insert(0, "3_model")
from np_model import NpExpert
expert = NpExpert("5_weights/AE/dis")      # 주행 전문가
cell_scores, pack_score = expert.scores(H) # H: (N, 12, 13) 55 s 이력
```

## 입력 · 판정

1. **입력**
   - 10 Hz 센싱값은 셀 전압 V1~V4, 셀 온도 T1~T4, 팩 전류 I입니다.
   - 10샘플을 평균해 1 s 윈도우를 만듭니다.
   - 5 s 간격 윈도우 12개(55 s 흐름)를 이력으로 씁니다.
2. **라우터**
   - 1 s 평균 전류가 > +0.03 A이면 충전, < −0.03 A이면 주행, 그 사이면 휴지로 봅니다.
   - 상태가 바뀌면 이력을 초기화합니다.
3. **전문가 AE**
   - 셀 AE: 셀마다 9개 특징 × 12를 봅니다. 특징은 다른 셀 대비 전압·온도 차이, 55 s 전 대비 변화, 기울기, 운전 문맥입니다. 4개 셀이 같은 가중치를 씁니다.
   - 팩 AE: 팩 전체 10개 특징 × 12를 봅니다.
4. **판정**
   - 복원오차를 τ로 나눈 값이 1을 넘으면 이상입니다.
   - τ는 경로별 정상 검증 데이터의 최대 오차 × 1.2입니다.
   - 오차가 가장 큰 셀을 이상 셀로 지목합니다.
   - 과충전 사양 규칙(최대 셀 > 4.205 V)을 함께 적용합니다.
