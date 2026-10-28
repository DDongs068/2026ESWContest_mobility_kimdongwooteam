import numpy as np
import pandas as pd

from features import build_history, WIN_HOP_S

I_REST: float = 0.03
I_COL: int = 12
EXPERTS: list[str] = ["chg", "dis", "rest"]


def window_state(S: np.ndarray) -> np.ndarray:
    I = S[:, I_COL]
    return np.where(I > I_REST, "chg", np.where(I < -I_REST, "dis", "rest"))


def segment_meta(meta: pd.DataFrame, state: np.ndarray) -> pd.DataFrame:
    m = meta.reset_index(drop=True).copy()
    m["_state"] = state
    order = m.sort_values(["source", "SimulationID", "WindowStart_s"]).index
    s = m.loc[order]
    new_run = (s["SimulationID"] != s["SimulationID"].shift()) | (s["source"] != s["source"].shift())
    seg = ((s["_state"] != s["_state"].shift()) | new_run).astype(int).groupby(
        [s["source"], s["SimulationID"]]).cumsum()
    m.loc[order, "_seg"] = seg.to_numpy()
    m["SimulationID"] = m["SimulationID"].astype(str) + "_s" + m["_seg"].astype(int).astype(str)
    return m.drop(columns=["_state", "_seg"])


def routed_history(S: np.ndarray, meta: pd.DataFrame, reset: bool = True) -> np.ndarray:
    if not reset:
        return build_history(S, meta)
    return build_history(S, segment_meta(meta, window_state(S)))


def route_of(H: np.ndarray) -> np.ndarray:
    return window_state(H[:, -1])


if __name__ == "__main__":
    try:
        n = 10
        meta = pd.DataFrame({"source": "t", "SimulationID": 1, "WindowStart_s": 5.0 + WIN_HOP_S * np.arange(n)})
        S = np.zeros((n, 13), dtype=np.float32)
        S[:, 0] = np.arange(n)
        S[3:7, I_COL] = -1.0
        H0, H1 = routed_history(S, meta, reset=False), routed_history(S, meta, reset=True)
        print(f"  상태: {window_state(S).tolist()}")
        print(f"  윈도우 4 이력 (리셋 없음): {H0[4, -4:, 0].astype(int).tolist()}  (리셋): "
              f"{H1[4, -4:, 0].astype(int).tolist()} (기대 [3, 3, 3, 4])")
        print(f"  경로: {route_of(H1).tolist()}")
    except Exception as e:
        print(f"[오류] router 스모크 테스트 실패: {e}")
        raise
