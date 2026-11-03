import numpy as np
import pandas as pd

V_COLS: list[str] = ["V1", "V2", "V3", "V4"]
T_COLS: list[str] = ["T1", "T2", "T3", "T4"]
FIT_ROWS: int = 10
DT_S: float = 0.1
NOISE_V: np.ndarray = np.full(4, 0.3e-3)
NOISE_I: float = 0.87e-3
NTC_SIGMA: tuple[float, float, float] = (0.15, 0.07, 0.025)
NTC_TAU_S: float = 60.0
LINE_COLS: list[str] = ["valid", *V_COLS, *T_COLS, "I", "STACK", "PACK", "LD"]


def load_sim_csv(path: str) -> pd.DataFrame:
    try:
        d = pd.read_csv(path)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"시뮬레이션 결과를 읽지 못함 ({path}): {e}") from e
    miss = [c for c in ["ms", *V_COLS, *T_COLS, "I", "STACK", "PACK"] if c not in d.columns]
    if miss or len(d) < FIT_ROWS:
        raise RuntimeError(f"시뮬레이션 결과 형식 오류 ({path}): 빠진 열 {miss}, {len(d)} 행")
    return d


def _ou(n: int, sigma: float, rng: np.random.Generator) -> np.ndarray:
    a = np.exp(-DT_S / NTC_TAU_S)
    x, b = np.zeros(n), sigma * np.sqrt(1.0 - a * a)
    x[0] = rng.normal(0.0, sigma)
    eps = rng.normal(0.0, 1.0, n)
    for k in range(1, n):
        x[k] = a * x[k - 1] + b * eps[k]
    return x


def _noise(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dv = rng.normal(0.0, 1.0, (n, 4)) * NOISE_V
    common = _ou(n, NTC_SIGMA[0], rng)
    dt = np.stack([common + _ou(n, NTC_SIGMA[1], rng) + rng.normal(0.0, NTC_SIGMA[2], n) for _ in range(4)], axis=1)
    dt -= dt[:FIT_ROWS].mean(axis=0)
    return dv, dt, rng.normal(0.0, NOISE_I, n)


def splice_fault(normal: pd.DataFrame, fault: pd.DataFrame, at_s: float) -> pd.DataFrame:
    head = normal[normal["ms"] < at_s * 1000]
    tail = fault.copy()
    tail["ms"] = tail["ms"] + int(round(at_s * 1000))
    return pd.concat([head, tail], ignore_index=True)


def fit_to_measured(sim: pd.DataFrame, snap: dict, seed: int, noise: bool = True,
                    keep_voltage: bool = False) -> tuple[pd.DataFrame, dict]:
    d, head = sim.copy(), sim.iloc[:FIT_ROWS]
    dv = np.asarray(snap["V"], float) - head[V_COLS].mean().to_numpy()
    dt = np.asarray(snap["T"], float) - head[T_COLS].mean().to_numpy()
    di = float(snap["I"]) - float(head["I"].mean())
    d[T_COLS] = d[T_COLS].to_numpy() + dt
    d["I"] = d["I"] + di
    if keep_voltage:
        dv = np.zeros(4)
    else:
        d[V_COLS] = d[V_COLS].to_numpy() + dv
        d["STACK"] = d["STACK"] + float(snap["STACK"]) - float(head["STACK"].mean())
        d["PACK"] = d["PACK"] + float(snap["PACK"]) - float(head["PACK"].mean())
    d["LD"] = float(snap.get("LD", 0.0))
    if noise:
        nv, nt, ni = _noise(len(d), np.random.default_rng(seed))
        d[V_COLS] = d[V_COLS].to_numpy() + nv
        d[T_COLS] = d[T_COLS].to_numpy() + nt
        d["I"] = d["I"] + ni
    d[V_COLS] = d[V_COLS].round(3)
    d[T_COLS] = d[T_COLS].round(1)
    d["I"] = d["I"].round(3)
    d[["STACK", "PACK", "LD"]] = d[["STACK", "PACK", "LD"]].round(2)
    d["valid"] = 1
    return d, {"V_mV": np.round(dv * 1e3, 1).tolist(), "T_C": np.round(dt, 2).tolist(), "I_A": round(di, 3)}


def to_line(row: pd.Series) -> str:
    return ",".join(str(row[c]) for c in LINE_COLS)


if __name__ == "__main__":
    try:
        n = 40
        t = pd.DataFrame({"ms": np.arange(n) * 100, **{c: 3.60 for c in V_COLS}, **{c: 24.0 for c in T_COLS},
                          "I": -0.8, "STACK": 14.4, "PACK": 14.4, "LD": 0.0})
        s = {"V": [3.7, 3.7, 3.7, 3.7], "T": [25.0] * 4, "I": -1.0, "STACK": 14.8, "PACK": 14.8, "LD": 14.8}
        out, err = fit_to_measured(t, s, seed=0, noise=False)
        assert abs(out["V1"].iloc[0] - 3.7) < 1e-9 and abs(out["I"].iloc[0] + 1.0) < 1e-9
        print("보정 크기:", err, "| 첫 줄:", to_line(out.iloc[0]))
    except (AssertionError, RuntimeError) as e:
        print(f"[오류] frames 스모크 테스트 실패: {e}")
        raise
