import os

import numpy as np
import pandas as pd

AI_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_CSV: str = os.path.join(AI_DIR, "raw", "normal_windows.csv")
SPLIT_CSV: str = os.path.join(AI_DIR, "data", "split_blocks.csv")
DATA_DIR: str = os.path.join(AI_DIR, "data")

WIN: int = 10
BLOCK_WIN: int = 180
HIST_WINDOWS: int = 55
GAP_MS: float = 2000.0
V_COLS: list[str] = ["V1", "V2", "V3", "V4"]
T_COLS: list[str] = ["T1", "T2", "T3", "T4"]
FILES: list[tuple[str, str, int, int]] = [
    ("3cycle", "C", 0, 12292), ("samples_1", "A", 12293, 22368), ("samples_2", "A", 22369, 29059),
    ("samples_3", "B", 29060, 39542), ("samples_4", "B", 39543, 55156), ("samples_0", "D", 55157, 64675)]


def load_windows() -> tuple[np.ndarray, pd.DataFrame]:
    d = pd.read_csv(RAW_CSV, encoding="utf-8-sig")
    if not (d.groupby("window_id").size() == WIN).all():
        raise ValueError("10행이 아닌 윈도우 존재")
    g = d.groupby("window_id", sort=False)
    m = g[V_COLS + T_COLS + ["I"]].mean()
    a = m.to_numpy(dtype=np.float32)
    S = np.concatenate([a[:, :8], np.zeros((len(a), 4), np.float32), a[:, 8:]], axis=1)
    w = pd.DataFrame({"window_id": m.index.to_numpy()})
    w["seq0"], w["seq1"] = g["seq"].first().to_numpy(), g["seq"].last().to_numpy()
    w["ms0"], w["ms1"] = g["ms"].first().to_numpy(), g["ms"].last().to_numpy()
    w["vmax_med"] = d[V_COLS].max(axis=1).groupby(d["window_id"], sort=False).median().to_numpy()
    w["file"], w["session"] = "", ""
    for name, ses, a0, a1 in FILES:
        k = w["window_id"].between(a0, a1)
        w.loc[k, "file"], w.loc[k, "session"] = name, ses
    return S, w


def assign_split(w: pd.DataFrame) -> pd.DataFrame:
    w = w.copy()
    new = (w["file"] != w["file"].shift()) | (w["seq0"] != w["seq1"].shift() + 1) | (w["ms0"] - w["ms1"].shift() > GAP_MS)
    w["seg"] = new.cumsum()
    w["source"] = w["file"]
    w["SimulationID"] = w["seg"].map("seg{:05d}".format)
    w["WindowStart_s"] = w.groupby("seg").cumcount().astype(float)
    w["fi"] = w.groupby("file").cumcount()
    w["block"] = w["file"] + "_" + (w["fi"] // BLOCK_WIN).map("{:04d}".format)
    w["split"] = w["block"].map(pd.read_csv(SPLIT_CSV, index_col="block")["split"])
    use = np.ones(len(w), dtype=int)
    change = np.where((w["split"] != w["split"].shift()) & (w["seg"] == w["seg"].shift()))[0]
    for k in change:
        use[k:k + HIST_WINDOWS] = 0
    w["use"] = use
    return w


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    S, w = load_windows()
    w = assign_split(w)
    np.savez(os.path.join(DATA_DIR, "summary_normal.npz"), S=S)
    w.to_csv(os.path.join(DATA_DIR, "meta_normal.csv"), index=False)
    u = w[w["use"] == 1]
    print("윈도우", len(w), "사용", len(u), u["split"].value_counts().to_dict())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"[오류] 전처리 실패: {e}")
