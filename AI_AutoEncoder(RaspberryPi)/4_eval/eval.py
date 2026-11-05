import json
import os
import sys

import numpy as np
import pandas as pd

AI_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AI_DIR, "3_model"))

from model import Expert, get_device, set_seed, SPEC_V
from router import routed_history, route_of, EXPERTS

DATA_DIR: str = os.path.join(AI_DIR, "data")
ABN_DIR: str = os.path.join(AI_DIR, "raw", "abnormal")
WEIGHTS_DIR: str = os.path.join(AI_DIR, "5_weights", "AE")
RES_DIR: str = os.path.join(AI_DIR, "4_eval", "results")
SEED: int = 42
WIN: int = 10
CTX_WINDOWS: int = 56
V_COLS: list[str] = ["V1", "V2", "V3", "V4"]
T_COLS: list[str] = ["T1", "T2", "T3", "T4"]
HW_RULES: dict[str, tuple[float, int]] = {"COV": (4.25, 5), "CUV": (2.80, 10), "OCD": (6.0, 1), "OT": (60.0, 10)}


def load_normal() -> tuple[np.ndarray, pd.DataFrame]:
    try:
        return np.load(os.path.join(DATA_DIR, "summary_normal.npz"))["S"], pd.read_csv(os.path.join(DATA_DIR, "meta_normal.csv"))
    except OSError as e:
        raise RuntimeError(f"정상 데이터 로드 실패 — 1_preprocess/preprocess.py 먼저: {e}") from e


def hw_trip_s(ab: pd.DataFrame) -> float:
    v, t, i = ab[V_COLS].to_numpy(), ab[T_COLS].to_numpy(), ab["I"].to_numpy()
    hits = {"COV": v.max(axis=1) > HW_RULES["COV"][0], "CUV": v.min(axis=1) < HW_RULES["CUV"][0],
            "OCD": -i > HW_RULES["OCD"][0], "OT": t.max(axis=1) > HW_RULES["OT"][0]}
    ms, best = ab["ms"].to_numpy(), np.inf
    for k, h in hits.items():
        run = 0
        for n, x in enumerate(h):
            run = run + 1 if x else 0
            if run >= HW_RULES[k][1]:
                best = min(best, float(ms[n]))
                break
    return best


def abnormal_stream(S0: np.ndarray, meta0: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    idx = pd.read_csv(os.path.join(ABN_DIR, "abnormal_index.csv"), encoding="utf-8-sig")
    idx = idx[idx["split"] == "test"]
    pos0 = pd.Series(np.arange(len(meta0)), index=meta0["window_id"])
    if set(idx["block"]) & set(meta0.loc[meta0["split"] == "train", "block"]):
        raise ValueError("학습 블록을 기준으로 한 비정상 파일 존재")
    Ss, ms = [], []
    for _, r in idx.iterrows():
        ab = pd.read_csv(os.path.join(ABN_DIR, "csv", r["file"]))
        n = len(ab) // WIN
        ab = ab.iloc[:n * WIN]
        g = np.repeat(np.arange(n), WIN)
        m = ab[V_COLS + T_COLS + ["I"]].groupby(g).mean().to_numpy(dtype=np.float32)
        S = np.concatenate([m[:, :8], np.zeros((n, 4), np.float32), m[:, 8:]], axis=1)
        p = int(pos0[r["w0"]])
        a = p
        while a > 0 and p - a < CTX_WINDOWS and meta0["seg"].iloc[a - 1] == meta0["seg"].iloc[p]:
            a -= 1
        c = p - a
        Ss.append(np.concatenate([S0[a:p], S]))
        onset = float(ab.loc[ab["fault_on"] == 1, "ms"].iloc[0]) if (ab["fault_on"] == 1).any() else np.inf
        ms.append(pd.DataFrame({
            "source": "abn", "SimulationID": r["file"], "WindowStart_s": np.arange(c + n, dtype=float),
            "vmax_med": np.r_[meta0["vmax_med"].iloc[a:p].to_numpy(), ab[V_COLS].max(axis=1).groupby(g).median().to_numpy()],
            "label": np.r_[np.zeros(c, int), ab["label"].groupby(g).last().to_numpy()],
            "fault_on": np.r_[np.zeros(c, int), ab["fault_on"].groupby(g).max().to_numpy()],
            "ms_end": np.r_[meta0["ms1"].iloc[a:p].to_numpy(), ab["ms"].groupby(g).last().to_numpy()],
            "onset_ms": onset, "hw_ms": hw_trip_s(ab), "scenario": r["scenario"], "R_ohm": r["R_ohm"], "cc": r["cc"], "cell": r["cell"]}))
    return np.concatenate(Ss), pd.concat(ms, ignore_index=True)


def predict(S: np.ndarray, meta: pd.DataFrame, experts: dict, thr: dict) -> pd.DataFrame:
    H = routed_history(S, meta, reset=True)
    route = route_of(H)
    sc, sp = np.zeros((len(H), 4), np.float32), np.zeros(len(H), np.float32)
    for r, ex in experts.items():
        m = route == r
        if m.any():
            sc[m], sp[m] = ex.scores(H[m])
    df = meta.copy()
    df["route"] = route
    tc = np.array([thr[r]["cell"] for r in route])
    tp = np.array([thr[r]["pack"] for r in route])
    df["ratio"] = np.maximum(sc.max(1) / tc, sp / tp)
    df["ae"] = (sc.max(1) > tc) | (sp > tp)
    df["pred"] = (df["ae"].to_numpy() | (df["vmax_med"].to_numpy() > SPEC_V)).astype(int)
    df["cell_guess"] = sc.argmax(1) + 1
    return df


def confusion(y: np.ndarray, p: np.ndarray) -> dict:
    tn, fp = int(((y == 0) & (p == 0)).sum()), int(((y == 0) & (p == 1)).sum())
    fn, tp = int(((y == 1) & (p == 0)).sum()), int(((y == 1) & (p == 1)).sum())
    return {"TN": tn, "FP": fp, "FN": fn, "TP": tp, "recall": round(tp / max(tp + fn, 1), 4),
            "precision": round(tp / max(tp + fp, 1), 4), "오경보율": round(fp / max(tn + fp, 1), 4)}


def main() -> None:
    set_seed(SEED)
    device = get_device()
    try:
        with open(os.path.join(WEIGHTS_DIR, "threshold.json"), encoding="utf-8") as f:
            thr = json.load(f)["experts"]
    except (OSError, KeyError) as e:
        raise RuntimeError(f"threshold 로드 실패: {e}") from e
    experts = {r: Expert(os.path.join(WEIGHTS_DIR, r), device) for r in EXPERTS}
    S, meta = load_normal()
    Sa, ma = abnormal_stream(S, meta)
    n = predict(S, meta, experts, thr)
    n = n[(n["split"] == "test") & (n["use"] == 1)]
    a = predict(Sa, ma, experts, thr)
    pre = a[a["fault_on"] == 0]
    d = a[a["label"] == 1]
    y = np.r_[np.zeros(len(n), int), np.ones(len(d), int)]
    res = confusion(y, np.r_[n["pred"].to_numpy(), d["pred"].to_numpy()])
    res["AE 단독 recall"] = round(float(d["ae"].mean()), 4)
    hit = d.groupby("SimulationID")["pred"].max()
    res["파일 탐지"] = f"{int(hit.sum())}/{len(hit)}"
    res["고장 전 경보"] = f"{int(pre['pred'].sum())}/{len(pre)}"
    first = a[(a["pred"] == 1) & (a["fault_on"] == 1)].groupby("SimulationID")["ms_end"].min()
    f = a.groupby("SimulationID")[["onset_ms", "hw_ms"]].first().join(first.rename("ae_ms"))
    res["고장 후 첫 경보 중앙 s"] = round(float(((f["ae_ms"] - f["onset_ms"]) / 1000).median()), 1)
    res["고장 후 첫 경보 최대 s"] = round(float(((f["ae_ms"] - f["onset_ms"]) / 1000).max()), 1)
    trip = f[np.isfinite(f["hw_ms"])]
    res["임계값 보호 동작 파일"] = f"{len(trip)}/{len(f)}"
    res["임계값 보호보다 먼저 경보"] = f"{int((trip['ae_ms'] < trip['hw_ms']).sum())}/{len(trip)}"
    res["선행 시간 중앙 s"] = round(float(((trip["hw_ms"] - trip["ae_ms"]) / 1000).median()), 1) if len(trip) else None
    table = pd.Series(res, name="값").to_string()
    scen = d.groupby("scenario")["pred"].mean().mul(100).round(1)
    sev = d.assign(sev=np.where(d["scenario"] == "overcharge", d["cc"].map("{:.2f}A".format), d["R_ohm"].map("{:.0f}Ω".format))
                   ).groupby(["scenario", "sev"])["pred"].mean().mul(100).round(1)
    fa = pd.Series(n["pred"].to_numpy()).groupby(n["route"].to_numpy()).agg(["size", "sum"])
    os.makedirs(RES_DIR, exist_ok=True)
    text = f"AE 평가 (1 s 판정)\n\n정상: 실측 test {len(n)} 윈도우 / 이상: 비정상 시뮬레이션 test {len(hit)} 파일 {len(d)} 윈도우\n\n{table}\n\n시나리오별 recall %\n{scen.to_string()}\n\n강도별 recall %\n{sev.to_string()}\n\n상태별 정상 오경보\n{fa.to_string()}\n"
    with open(os.path.join(RES_DIR, "results.txt"), "w", encoding="utf-8") as fw:
        fw.write(text)
    sev.rename("recall_%").to_csv(os.path.join(RES_DIR, "compare.csv"), encoding="utf-8-sig")
    print(text)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        raise SystemExit(f"[오류] 평가 실패: {e}")
