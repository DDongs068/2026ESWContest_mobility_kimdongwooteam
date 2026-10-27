"""
preprocess.py — 시뮬레이션 원본 run (0.1 s 샘플) → 1 s 윈도우 요약 (10 샘플 평균, 1 s 간격) + 메타/라벨

입력 (RAW_DIR, 저장소에 포함하지 않음 — Simscape 디지털 트윈으로 생성한 원본):
    정상  : raw/normal/run_<id>_<kind>.csv              (4,241 run: rest · accel · steady · charge)
    fault3: raw/fault3/run_<id>_<kind>.csv + _meta.csv  (평가 전용: 위험 soft ISC R 1~25 Ω 60 run + 같은 프로파일 정상 60 run)
    분할  : data/split_runs.csv  (run 단위 train / val / test — 저장소 포함)
    원본 열: time_s, phase, CellV1..4, Temp1..4, Bal1..4, PackI, AnyFault, ...

처리:
    밸런싱 B = 0 (AE 는 밸런싱 상태를 입력으로 쓰지 않음)
    온도 보강 — 실측 NTC 센서 특성 (원본 0.1 s 샘플 단계, run 마다 결정적 시드 = run id):
        T_k(t) += s·[ OU_common(σ 0.15, τ 60 s) + OU_k(σ 0.07, τ 60 s) + N(0, 0.025²) ],  s ~ U(0.5, 1.5)  → 0.1 °C 반올림
        근거: 실제 회로 정상 측정 (빠른 잡음 σ 0.025, 공통 느린 σ 0.15, 셀별 σ 0.07 °C, 상관 시간 ≈ 60 s)
        OU 이산식 x[k+1] = a·x[k] + σ√(1−a²)·ε,  a = exp(−Δt/τ)  (scipy.signal.lfilter)
        시뮬레이션의 매끈한 온도만 학습하면 실제 센서의 작은 온도 흔들림도 이상으로 판정 → 정상 데이터에 센서 특성을 넣어 해결
        (가짜 이상 데이터는 만들지 않음 — 학습은 정상만)
    윈도우: time_s < 5 s (warmup) 제외 / rest run 은 윈도우 10 샘플이 모두 phase 0 인 것만 / 시작 = 10 샘플 간격
요약 S (13) = V̄1..4, T̄1..4, B̄1..4, Ī  +  vmax_med (윈도우 최대셀 전압 중앙값, 과충전 사양 규칙)
fault3 라벨 (윈도우 [s, s+1 s]):  정상 run → 0 / ISC run: s ≥ 고장 시작 → 1, s + 1 ≤ 고장 시작 → 0, 걸침 → −1
    trip_s = BMS 하드웨어 보호(AnyFault) 최초 시각

Output (DATA_DIR): summary_normal.npz / meta_normal.csv, summary_fault3.npz / meta_fault3.csv (온도 보강),
                   summary_fault3_clean.npz / meta_fault3_clean.csv (원본 온도)
Usage:
    python 1_preprocess/preprocess.py
"""

import glob
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from scipy.signal import lfilter

# ===== 경로 상수 =====
AI_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # AI_AutoEncoder/
RAW_DIR: str = os.path.join(AI_DIR, "raw")                                       # 시뮬레이션 원본 (별도 준비)
RAW_NORMAL: str = os.path.join(RAW_DIR, "normal")
RAW_F3: str = os.path.join(RAW_DIR, "fault3")
DATA_DIR: str = os.path.join(AI_DIR, "data")
SPLIT_CSV: str = os.path.join(DATA_DIR, "split_runs.csv")

# ===== 상수 =====
NTC_DT: float = 0.1
NTC_TAU_S: float = 60.0
NTC_SIGMA: tuple[float, float, float] = (0.15, 0.07, 0.025)     # 공통 느린 / 셀별 느린 / 빠른 잡음 [°C]
NTC_SCALE: tuple[float, float] = (0.5, 1.5)                      # run 별 세기 배수 범위
WIN: int = 10                 # 1 s 윈도우
WARMUP_S: float = 5.0
SIG: list[str] = ["CellV1", "CellV2", "CellV3", "CellV4", "Temp1", "Temp2", "Temp3", "Temp4",
                  "Bal1", "Bal2", "Bal3", "Bal4", "PackI"]               # 요약 S 순서
SCN: dict[str, str] = {"rest": "rest", "accel": "accel_drive", "steady": "steady_drive", "charge": "charge",
                       "drive": "mixed_drive"}
N_PROC: int = 8


def ntc_noise(n: int, seed: int) -> np.ndarray:
    """실측 NTC 특성 (n, 4): s·(공통 OU + 셀별 OU + 백색 잡음). run 마다 결정적."""
    rng = np.random.default_rng(seed)
    s = rng.uniform(*NTC_SCALE)
    a = np.exp(-NTC_DT / NTC_TAU_S)
    sc, si, sw = NTC_SIGMA

    def ou(sig: float, k: int) -> np.ndarray:
        e = rng.standard_normal((n, k)) * sig * np.sqrt(1 - a * a)
        e[0] = rng.standard_normal(k) * sig                              # 정상상태에서 시작
        return lfilter([1.0], [1.0, -a], e, axis=0)

    return s * (ou(sc, 1) + ou(si, 4) + sw * rng.standard_normal((n, 4)))


def summarize_run(path: str, ntc_on: bool = True) -> tuple[np.ndarray, pd.DataFrame]:
    """
    run CSV 1 개 → 1 s 윈도우 요약.
    Returns: S (N, 13) float32, meta [SimulationID, kind, WindowStart_s, vmax_med, trip_s]
    """
    base = os.path.basename(path)
    rid, kind = int(base.split("_")[1]), base.split("_")[2].replace(".csv", "")
    try:
        d = pd.read_csv(path, usecols=["time_s", "phase", *SIG, "AnyFault"])
    except (OSError, ValueError) as e:
        raise RuntimeError(f"run 읽기 실패: {path}: {e}") from e
    t, ph = d["time_s"].to_numpy(), d["phase"].to_numpy().astype(int)
    d[["Bal1", "Bal2", "Bal3", "Bal4"]] = 0.0                               # 밸런싱 0 (AE 입력 미사용)
    T = d[["Temp1", "Temp2", "Temp3", "Temp4"]].to_numpy(dtype=np.float64)
    if ntc_on:
        T = T + ntc_noise(len(d), rid)
    d[["Temp1", "Temp2", "Temp3", "Temp4"]] = np.round(T, 1)                # 0.1 °C 해상도 (v2 와 같음)
    X = d[SIG].to_numpy(dtype=np.float32)                                   # (T, 13)
    V = d[["CellV1", "CellV2", "CellV3", "CellV4"]].to_numpy(dtype=np.float32)
    first = int(np.searchsorted(t, WARMUP_S - 1e-9))
    starts = [s for s in range(first, len(d) - WIN + 1, WIN) if not (kind == "rest" and np.any(ph[s:s + WIN] != 0))]
    if not starts:
        return np.zeros((0, 13), np.float32), pd.DataFrame()
    idx = np.array(starts)[:, None] + np.arange(WIN)[None, :]               # (N, 10)
    S = X[idx].mean(axis=1)                                                  # (N, 13)
    vmax = np.median(V[idx].max(axis=2), axis=1)                             # (N,)
    trip = t[d["AnyFault"].to_numpy() > 0]
    meta = pd.DataFrame({"SimulationID": rid, "kind": kind, "WindowStart_s": np.round(t[starts], 3),
                         "vmax_med": vmax, "trip_s": float(trip.min()) if len(trip) else np.inf})
    return S.astype(np.float32), meta


def summarize_dir(raw_dir: str, ntc_on: bool = True) -> tuple[np.ndarray, pd.DataFrame]:
    """폴더의 모든 run (병렬)."""
    paths = sorted(p for p in glob.glob(os.path.join(raw_dir, "run_*.csv")) if not p.endswith("_meta.csv"))
    if not paths:
        raise FileNotFoundError(f"run 없음: {raw_dir}")
    S_list, M_list = [], []
    with ProcessPoolExecutor(N_PROC) as ex:
        for i, (S, m) in enumerate(ex.map(summarize_run, paths, [ntc_on] * len(paths), chunksize=16), 1):
            if len(S):
                S_list.append(S)
                M_list.append(m)
            if i % 500 == 0 or i == len(paths):
                print(f"    {os.path.basename(raw_dir)}: {i}/{len(paths)} run", flush=True)
    return np.concatenate(S_list), pd.concat(M_list, ignore_index=True)


def split_map() -> dict[int, str]:
    """run 번호 → train / val / test (data/split_runs.csv)."""
    try:
        d = pd.read_csv(SPLIT_CSV)
    except OSError as e:
        raise RuntimeError(f"분할 목록 로드 실패: {SPLIT_CSV}: {e}") from e
    return {int(r): sp for r, sp in zip(d["run_id"], d["split"])}


def fault3_labels(meta: pd.DataFrame) -> pd.DataFrame:
    """fault3 run 메타 병합 + 윈도우 라벨."""
    rows = []
    for p in glob.glob(os.path.join(RAW_F3, "run_*_meta.csv")):
        try:
            rows.append(pd.read_csv(p).iloc[0])
        except (OSError, IndexError, ValueError) as e:
            raise RuntimeError(f"fault3 메타 읽기 실패: {p}: {e}") from e
    rm = pd.DataFrame(rows)[["run_id", "fault_type", "fault_mag", "fault_cell", "fault_start_s", "isc_mode", "twin", "tamb_C"]]
    m = meta.merge(rm.rename(columns={"run_id": "SimulationID"}), on="SimulationID", how="left", validate="many_to_one")
    s, t0, isc = m["WindowStart_s"].to_numpy(), m["fault_start_s"].to_numpy(), (m["fault_type"] == "softisc").to_numpy()
    m["label"] = np.where(~isc, 0, np.where(s >= t0, 1, np.where(s + 1.0 <= t0, 0, -1)))
    m["twin"] = m["twin"].astype(int)
    return m


def save(name: str, S: np.ndarray, meta: pd.DataFrame) -> None:
    """원자적 저장."""
    p = os.path.join(DATA_DIR, f"summary_{name}.npz")
    with open(p + ".tmp", "wb") as f:
        np.savez(f, S=S)
    os.replace(p + ".tmp", p)
    meta.to_csv(os.path.join(DATA_DIR, f"meta_{name}.csv.tmp"), index=False)
    os.replace(os.path.join(DATA_DIR, f"meta_{name}.csv.tmp"), os.path.join(DATA_DIR, f"meta_{name}.csv"))
    print(f"  저장: summary_{name}.npz {S.shape}, meta_{name}.csv {meta.shape}")


def main() -> None:
    """전처리 메인 (이미 있으면 건너뜀)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(DATA_DIR, "summary_normal.npz")):
        print("[normal] 1 s 윈도우 요약")
        S, m = summarize_dir(RAW_NORMAL)
        sp = split_map()
        m["split"] = m["SimulationID"].map(sp)
        keep = m["split"].notna().to_numpy()
        S, m = S[keep], m[keep].reset_index(drop=True)
        m["scenario"] = m["kind"].map(SCN)
        m["source"] = "normal"
        print(f"  split: {m['split'].value_counts().to_dict()} (분할에 없는 run 제외 {int((~keep).sum())} 윈도우)")
        save("normal", S, m)
    for name, on in [("fault3", True), ("fault3_clean", False)]:
        if os.path.exists(os.path.join(DATA_DIR, f"summary_{name}.npz")):
            continue
        print(f"[{name}] 1 s 윈도우 요약 (온도 보강 {'켬' if on else '끔'})")
        S, m = summarize_dir(RAW_F3, on)
        m = fault3_labels(m)
        m["scenario"] = m["kind"].map(SCN)
        m["source"] = "fault3"
        print(f"  라벨: {m['label'].value_counts().to_dict()}")
        save(name, S, m)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[오류] 전처리 실패: {e}")
        raise
