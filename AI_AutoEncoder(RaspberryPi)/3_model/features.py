import numpy as np
import pandas as pd

L_HIST: int = 12
WIN_HOP_S: float = 1.0
HIST_STEP: int = 5
HOP_S: float = WIN_HOP_S * HIST_STEP
RUN_KEYS: list[str] = ["source", "SimulationID"]
SUMMARY_NAMES: list[str] = [f"V{i}" for i in range(1, 5)] + [f"T{i}" for i in range(1, 5)] + \
                           [f"B{i}" for i in range(1, 5)] + ["I"]
CELL_FEATURES: list[str] = ["dV", "tdV", "gdV", "dT", "tdT", "B", "I", "V_pack", "T_pack"]
PACK_FEATURES: list[str] = ["V_mean", "V_max", "V_spread", "gV_mean", "T_mean", "T_max", "T_spread",
                            "gT_mean", "I", "B_any"]
N_CELL_F: int = len(CELL_FEATURES)
N_PACK_F: int = len(PACK_FEATURES)
Z_CLIP: float = 20.0
WINSOR: tuple[float, float] = (0.5, 99.5)
SCALE_FLOOR: float = 1e-6


def build_history(S: np.ndarray, meta: pd.DataFrame, L: int = L_HIST, step: int = HIST_STEP) -> np.ndarray:
    missing = [c for c in RUN_KEYS + ["WindowStart_s"] if c not in meta.columns]
    if missing:
        raise ValueError(f"meta 컬럼 없음: {missing}")
    out = np.empty((len(S), L, S.shape[1]), dtype=np.float32)
    m = meta.reset_index(drop=True)
    for key, g in m.groupby(RUN_KEYS, sort=False):
        g = g.sort_values("WindowStart_s")
        rows = g.index.to_numpy()
        gaps = np.diff(g["WindowStart_s"].to_numpy())
        if len(gaps) and not np.allclose(gaps, WIN_HOP_S):
            raise ValueError(f"run {key} hop 불연속: {np.unique(gaps)}")
        pos = np.arange(len(rows))[:, None] + (np.arange(-(L - 1), 1) * step)[None, :]
        out[rows] = S[rows[np.clip(pos, 0, None)]]
    return out


def cell_features(H: np.ndarray) -> np.ndarray:
    V, T, B, I = H[:, :, 0:4], H[:, :, 4:8], H[:, :, 8:12], H[:, :, 12]
    others_V = (V.sum(axis=2, keepdims=True) - V) / 3.0
    others_T = (T.sum(axis=2, keepdims=True) - T) / 3.0
    dV, dT = V - others_V, T - others_T
    tdV, tdT = dV - dV[:, :1], dT - dT[:, :1]
    gdV = np.concatenate([np.zeros_like(dV[:, :1]), np.diff(dV, axis=1) / HOP_S], axis=1)
    ctx = [np.repeat(x[:, :, None], 4, axis=2) for x in (I, V.mean(axis=2), T.mean(axis=2))]
    f = np.stack([dV, tdV, gdV, dT, tdT, B] + ctx, axis=2)
    return f.transpose(0, 3, 2, 1).astype(np.float32)


def pack_features(H: np.ndarray) -> np.ndarray:
    V, T, B, I = H[:, :, 0:4], H[:, :, 4:8], H[:, :, 8:12], H[:, :, 12]
    vm, tm = V.mean(axis=2), T.mean(axis=2)

    def grad(x: np.ndarray) -> np.ndarray:
        return np.concatenate([np.zeros_like(x[:, :1]), np.diff(x, axis=1) / HOP_S], axis=1)

    f = np.stack([vm, V.max(axis=2), V.max(axis=2) - V.min(axis=2), grad(vm),
                  tm, T.max(axis=2), T.max(axis=2) - T.min(axis=2), grad(tm), I, B.mean(axis=2)], axis=1)
    return f.astype(np.float32)


def compute_stats(F: np.ndarray, names: list[str]) -> dict:
    flat = F.transpose(1, 0, 2).reshape(F.shape[1], -1)
    lo, hi = np.percentile(flat, WINSOR, axis=1)
    w = np.clip(flat, lo[:, None], hi[:, None])
    return {"features": names, "median": np.median(flat, axis=1).tolist(),
            "scale": np.maximum(w.std(axis=1), SCALE_FLOOR).tolist()}


def normalize(F: np.ndarray, stats: dict) -> np.ndarray:
    med = np.asarray(stats["median"], dtype=np.float32)[None, :, None]
    sc = np.asarray(stats["scale"], dtype=np.float32)[None, :, None]
    return np.clip((F - med) / sc, -Z_CLIP, Z_CLIP).astype(np.float32)


if __name__ == "__main__":
    try:
        n = 60
        meta = pd.DataFrame({"source": "t", "SimulationID": 1, "WindowStart_s": 5.0 + WIN_HOP_S * np.arange(n)})
        S = np.zeros((n, 13), dtype=np.float32)
        S[:, 0:4] = 3.7 + 0.002 * np.arange(n)[:, None]
        S[:, 0] -= 0.0002 * np.arange(n)
        S[:, 4:8] = 25.0
        S[:, 4] += 0.01 * np.arange(n)
        S[:, 12] = -0.2
        H = build_history(S, meta)
        Fc, Fp = cell_features(H), pack_features(H)
        print(f"  S {S.shape} → H {H.shape} → cell {Fc.shape}, pack {Fp.shape}")
        print(f"  마지막 tdV [mV]: {(Fc[-1, :, 1, -1] * 1e3).round(2).tolist()} (기대 [-11, 3.67, 3.67, 3.67])")
        Z = normalize(Fp, compute_stats(Fp, PACK_FEATURES))
        print(f"  pack normalize {Z.shape}, |z| max {np.abs(Z).max():.2f}")
    except Exception as e:
        print(f"[오류] features 스모크 테스트 실패: {e}")
        raise
