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
WEIGHTS_DIR: str = os.path.join(AI_DIR, "5_weights", "AE")
RES_DIR: str = os.path.join(AI_DIR, "4_eval", "results")
SEED: int = 42
WIN_S: float = 1.0


def load(name: str) -> tuple[np.ndarray, pd.DataFrame]:
    try:
        return np.load(os.path.join(DATA_DIR, f"summary_{name}.npz"))["S"], pd.read_csv(os.path.join(DATA_DIR, f"meta_{name}.csv"))
    except OSError as e:
        raise RuntimeError(f"{name} 로드 실패 — 1_preprocess/preprocess.py 먼저: {e}") from e


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
    tc = np.array([thr[r]["cell"] for r in route]); tp = np.array([thr[r]["pack"] for r in route])
    df["ratio"] = np.maximum(sc.max(1) / tc, sp / tp)
    df["pred"] = (((sc.max(1) > tc) | (sp > tp)) | (df["vmax_med"].to_numpy() > SPEC_V)).astype(int)
    df["cell_guess"] = sc.argmax(1) + 1
    df["t_end"] = df["WindowStart_s"] + WIN_S
    return df


def events(c: np.ndarray) -> int:
    return int((c[1:] & ~c[:-1]).sum() + c[:1].sum())


def confusion(df: pd.DataFrame) -> dict:
    y, p = df["label"].to_numpy(), df["pred"].to_numpy()
    tn, fp = int(((y == 0) & (p == 0)).sum()), int(((y == 0) & (p == 1)).sum())
    fn, tp = int(((y == 1) & (p == 0)).sum()), int(((y == 1) & (p == 1)).sum())
    return {"TN": tn, "FP": fp, "FN": fn, "TP": tp, "recall": round(tp / max(tp + fn, 1), 4),
            "precision": round(tp / max(tp + fp, 1), 4), "오경보율": round(fp / max(tn + fp, 1), 4)}


def fault3_metrics(df: pd.DataFrame) -> dict:
    ev, secs, on_s, delays, post, cell_ok, lead, hw_runs, ae_first = 0, 0.0, 0.0, [], [], [], [], 0, 0
    for _, g in df.groupby("SimulationID"):
        g = g.sort_values("WindowStart_s")
        t, c = g["t_end"].to_numpy(), g["pred"].to_numpy().astype(bool)
        isc = g["fault_type"].iloc[0] == "softisc"
        onset = float(g["fault_start_s"].iloc[0]) if isc else np.inf
        pre = t <= onset
        ev += events(c[pre]); secs += pre.sum() * WIN_S; on_s += c[pre].sum() * WIN_S
        if not isc:
            continue
        hit = np.where((t >= onset) & c)[0]
        t_ae = t[hit[0]] if len(hit) else np.inf
        if len(hit):
            delays.append(t_ae - onset)
            cell_ok.append(int(g["cell_guess"].iloc[hit[0]] == g["fault_cell"].iloc[0]))
        post.append(c[g["WindowStart_s"].to_numpy() >= onset].mean())
        trip = float(g["trip_s"].iloc[0])
        if np.isfinite(trip) and trip >= onset:
            hw_runs += 1
            ae_first += int(t_ae < trip)
            if t_ae < trip:
                lead.append(trip - t_ae)
    return {"fa_h": ev / (secs / 3600), "alarm_pct": 100 * on_s / secs, "det": f"{len(delays)}/{len(post)}",
            "delay_med": float(np.median(delays)), "delay_max": float(np.max(delays)),
            "post_recall": 100 * float(np.mean(post)), "cell_ok": 100 * float(np.mean(cell_ok)),
            "hw": f"{ae_first}/{hw_runs}", "lead_med": float(np.median(lead)) if lead else float("nan"),
            "no_hw": len(post) - hw_runs}


def normal_test_fa(df: pd.DataFrame) -> float:
    ev, secs = 0, 0.0
    for _, g in df.groupby("SimulationID"):
        c = g.sort_values("WindowStart_s")["pred"].to_numpy().astype(bool)
        ev += events(c); secs += len(c) * WIN_S
    return ev / (secs / 3600)


def main() -> None:
    set_seed(SEED)
    device = get_device()
    try:
        with open(os.path.join(WEIGHTS_DIR, "threshold.json"), encoding="utf-8") as f:
            thr = json.load(f)["experts"]
        experts = {r: Expert(os.path.join(WEIGHTS_DIR, r), device) for r in EXPERTS}
    except (OSError, KeyError) as e:
        raise RuntimeError(f"AE 로드 실패 ({WEIGHTS_DIR}): {e}") from e
    Sn, mn = load("normal")
    te = (mn["split"] == "test").to_numpy()
    nt = normal_test_fa(predict(Sn, mn, experts, thr)[te])
    rows = []
    for name, label in [("fault3", "온도 실측 센서 특성 반영"), ("fault3_clean", "원본 온도")]:
        S3, m3 = load(name)
        d3 = predict(S3, m3, experts, thr)
        d3 = d3[d3["label"] >= 0]
        m = fault3_metrics(d3)
        rows.append({"데이터": f"fault3 ({label})", **confusion(d3),
                     "정상구간 오경보/h": round(m["fa_h"], 1), "경보 시간 %": round(m["alarm_pct"], 2),
                     "ISC 탐지": m["det"], "지연 중앙 s": round(m["delay_med"], 1), "지연 최대 s": round(m["delay_max"], 1),
                     "고장 후 탐지율 %": round(m["post_recall"], 1), "위치 정답 %": round(m["cell_ok"], 1),
                     "HW 보호보다 먼저": m["hw"], "선행 중앙 s": round(m["lead_med"], 1), "HW 보호 없음 run": m["no_hw"],
                     "정상 test 오경보/h": round(nt, 2)})
        print(f"  {name} 완료", flush=True)
    res = pd.DataFrame(rows)
    os.makedirs(RES_DIR, exist_ok=True)
    res.to_csv(os.path.join(RES_DIR, "compare.csv"), index=False)
    text = ("AE 평가 (1 s 판정, 연속 확인 1 회)\n\n" + res.T.to_string(header=False) +
            "\n\n판정 경계 τ (경로별 정상 val 최대 × 1.2)\n" +
            json.dumps({r: {"cell": round(v["cell"], 3), "pack": round(v["pack"], 3)} for r, v in thr.items()}, indent=1))
    with open(os.path.join(RES_DIR, "results.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[오류] AE 평가 실패: {e}")
        raise
