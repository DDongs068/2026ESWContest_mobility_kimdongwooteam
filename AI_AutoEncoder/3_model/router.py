"""
router.py — 학습 없는 센싱 기반 라우터: 운전 상태 판별 · 상태 구간별 이력 리셋 · 전문가 경로 선택

입력은 윈도우 요약 S (13) 뿐 (충전기 연결 등 별도 플래그 사용 안 함)
    상태 : Ī > I_REST → chg (충전),  Ī < −I_REST → dis (주행·방전),  나머지 → rest (휴지)
    리셋 : 같은 run 안에서 상태가 바뀐 지점부터 새 구간 → 이력은 그 구간 안에서만 쌓음
           (정상 학습 데이터는 run 마다 상태가 하나뿐 → 리셋하면 전환 직후 입력도 학습 때와 같은 형태)
    경로 : 현재 윈도우 상태 = 전문가 이름 (chg / dis / rest)
    ※ 셀 전압·온도는 라우팅에 쓰지 않음 → 고장(ISC)이 경로를 바꿀 수 없음

Functions:
    window_state(S)                 : (N, 13) → (N,) 상태
    segment_meta(meta, state)       : run 키에 상태 구간 번호를 붙인 meta
    routed_history(S, meta, reset)  : (N, 13) → (N, L, 13)
    route_of(H)                     : (N, L, 13) → (N,) 전문가 경로

Usage:
    H = routed_history(S, meta); r = route_of(H)
"""

import numpy as np
import pandas as pd

from features import build_history, WIN_HOP_S

# ===== 상수 =====
I_REST: float = 0.03
I_COL: int = 12
EXPERTS: list[str] = ["chg", "dis", "rest"]


def window_state(S: np.ndarray) -> np.ndarray:
    """윈도우 평균 전류로 상태.  (N, 13) → (N,) {chg, dis, rest}"""
    I = S[:, I_COL]
    return np.where(I > I_REST, "chg", np.where(I < -I_REST, "dis", "rest"))


def segment_meta(meta: pd.DataFrame, state: np.ndarray) -> pd.DataFrame:
    """
    같은 run 안에서 상태가 바뀔 때마다 구간 번호 +1 → SimulationID 를 "<id>_s<구간>" 로 바꾼 meta.

    Args:
        meta: [source, SimulationID, WindowStart_s] (S 와 같은 행 순서)
        state: (N,) window_state 결과
    Returns:
        구간 단위 meta (원본 행 순서 유지)
    """
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
    """이력 구성.  (N, 13) → (N, L, 13);  reset=True 면 상태 구간 안에서만 이력."""
    if not reset:
        return build_history(S, meta)
    return build_history(S, segment_meta(meta, window_state(S)))


def route_of(H: np.ndarray) -> np.ndarray:
    """현재 윈도우의 전문가 경로.  (N, L, 13) → (N,)"""
    return window_state(H[:, -1])


if __name__ == "__main__":
    # 스모크 테스트: rest 3 → dis 4 → rest 3 → 리셋 이력이 구간 경계를 넘지 않는지
    try:
        n = 10
        meta = pd.DataFrame({"source": "t", "SimulationID": 1, "WindowStart_s": 5.0 + WIN_HOP_S * np.arange(n)})
        S = np.zeros((n, 13), dtype=np.float32)
        S[:, 0] = np.arange(n)                                               # 윈도우 번호를 V1 에 기록
        S[3:7, I_COL] = -1.0
        H0, H1 = routed_history(S, meta, reset=False), routed_history(S, meta, reset=True)
        print(f"  상태: {window_state(S).tolist()}")
        print(f"  윈도우 4 이력 (리셋 없음): {H0[4, -4:, 0].astype(int).tolist()}  (리셋): "
              f"{H1[4, -4:, 0].astype(int).tolist()} (기대 [3, 3, 3, 4])")
        print(f"  경로: {route_of(H1).tolist()}")
    except Exception as e:
        print(f"[오류] router 스모크 테스트 실패: {e}")
        raise
