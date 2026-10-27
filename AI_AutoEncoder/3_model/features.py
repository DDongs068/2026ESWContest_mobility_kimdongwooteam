"""
features.py — 윈도우 요약 → 이력(윈도우들의 윈도우) → 셀 관점 / 팩 관점 특징 → 정규화

윈도우 = 10 샘플 (1 s 평균), 윈도우 간격 1 s → 이력은 HIST_STEP(5) 개씩 건너뛰어 5 s 간격 12 개 (55 s)

입력 재료: 윈도우 요약 S (13) = [V̄1..4, T̄1..4, B̄1..4, Ī]   (1_preprocess/preprocess.py 가 생성, 밸런싱 B̄ = 0)
이력      : 같은 run(또는 같은 상태 구간)의 최근 L = 12 윈도우 (hop 5 s → 55 s), 미래 사용 안 함
            이력이 부족하면 가장 오래된 윈도우를 반복해서 채움

[셀 관점] 셀 i 마다 (4개 셀이 같은 AE 공유) — 9 특징 × L
    dV_i  = V̄_i − mean_{j≠i} V̄_j           셀 간 전압 차이 (공통 부하·SOC 성분 제거)
    tdV_i = dV_i(k) − dV_i(0)               55 s 전 대비 처짐
    gdV_i = (dV_i(k) − dV_i(k−1)) / 5 s     윈도우 간 기울기
    dT_i  = T̄_i − mean_{j≠i} T̄_j           셀 간 온도 차이
    tdT_i = dT_i(k) − dT_i(0)               55 s 전 대비 발열
    B_i                                     이 셀 밸런싱 동작 비율 (AE 는 밸런싱 미사용 → 항상 0)
    Ī, V̄_pack, T̄_pack                      공통 운전 문맥
[팩 관점] 4셀 전체 (셀 순서 무관) — 10 특징 × L
    V_mean, V_max, V_spread, gV_mean, T_mean, T_max, T_spread, gT_mean, I, B_any

정규화: z = clip((x − median) / σ_w, ±20),  σ_w = 0.5~99.5 % winsorize 후 std (전문가별 정상 train 통계)

Functions:
    build_history(S, meta, L)  : (N, 13) → (N, L, 13)
    cell_features(H)           : (N, L, 13) → (N, 4, 9, L)
    pack_features(H)           : (N, L, 13) → (N, 10, L)
    compute_stats / normalize  : 정규화 통계 / 적용

Usage:
    H = build_history(S, meta); Zc = normalize(cell_features(H).reshape(-1, 9, L_HIST), stats_cell)
"""

import numpy as np
import pandas as pd

# ===== 상수 =====
L_HIST: int = 12
WIN_HOP_S: float = 1.0       # 윈도우 간격 (1 s 윈도우를 1 s 마다)
HIST_STEP: int = 5           # 이력 윈도우 간격 = 5 개 = 5 s
HOP_S: float = WIN_HOP_S * HIST_STEP     # 이력 내 윈도우 간격 [s] (기울기 gdV 등 계산 단위 = 5 s)
RUN_KEYS: list[str] = ["source", "SimulationID"]
SUMMARY_NAMES: list[str] = [f"V{i}" for i in range(1, 5)] + [f"T{i}" for i in range(1, 5)] + \
                           [f"B{i}" for i in range(1, 5)] + ["I"]
CELL_FEATURES: list[str] = ["dV", "tdV", "gdV", "dT", "tdT", "B", "I", "V_pack", "T_pack"]
PACK_FEATURES: list[str] = ["V_mean", "V_max", "V_spread", "gV_mean", "T_mean", "T_max", "T_spread",
                            "gT_mean", "I", "B_any"]
N_CELL_F: int = len(CELL_FEATURES)       # 9
N_PACK_F: int = len(PACK_FEATURES)       # 10
Z_CLIP: float = 20.0
WINSOR: tuple[float, float] = (0.5, 99.5)
SCALE_FLOOR: float = 1e-6


def build_history(S: np.ndarray, meta: pd.DataFrame, L: int = L_HIST, step: int = HIST_STEP) -> np.ndarray:
    """
    같은 run 의 현재 윈도우 + step 개씩 건너뛴 과거 L−1 개 (1 s 윈도우 → 5 s 간격).

    Args:
        S: (N, 13) 윈도우 요약 (meta 와 같은 행 순서)
        meta: RUN_KEYS + WindowStart_s
    Returns:
        (N, L, 13)
    """
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
    """셀 관점 특징.  (N, L, 13) → (N, 4, 9, L)"""
    V, T, B, I = H[:, :, 0:4], H[:, :, 4:8], H[:, :, 8:12], H[:, :, 12]     # (N, L, 4) ×3, (N, L)
    others_V = (V.sum(axis=2, keepdims=True) - V) / 3.0                      # (N, L, 4) 나머지 3셀 평균
    others_T = (T.sum(axis=2, keepdims=True) - T) / 3.0
    dV, dT = V - others_V, T - others_T                                      # (N, L, 4)
    tdV, tdT = dV - dV[:, :1], dT - dT[:, :1]
    gdV = np.concatenate([np.zeros_like(dV[:, :1]), np.diff(dV, axis=1) / HOP_S], axis=1)
    ctx = [np.repeat(x[:, :, None], 4, axis=2) for x in (I, V.mean(axis=2), T.mean(axis=2))]   # (N, L, 4) ×3
    f = np.stack([dV, tdV, gdV, dT, tdT, B] + ctx, axis=2)                  # (N, L, 9, 4)
    return f.transpose(0, 3, 2, 1).astype(np.float32)                        # (N, 4, 9, L)


def pack_features(H: np.ndarray) -> np.ndarray:
    """팩 관점 특징 (셀 순서 무관).  (N, L, 13) → (N, 10, L)"""
    V, T, B, I = H[:, :, 0:4], H[:, :, 4:8], H[:, :, 8:12], H[:, :, 12]
    vm, tm = V.mean(axis=2), T.mean(axis=2)                                  # (N, L)

    def grad(x: np.ndarray) -> np.ndarray:
        """윈도우 간 기울기 (첫 칸 0).  (N, L) → (N, L)"""
        return np.concatenate([np.zeros_like(x[:, :1]), np.diff(x, axis=1) / HOP_S], axis=1)

    f = np.stack([vm, V.max(axis=2), V.max(axis=2) - V.min(axis=2), grad(vm),
                  tm, T.max(axis=2), T.max(axis=2) - T.min(axis=2), grad(tm), I, B.mean(axis=2)], axis=1)
    return f.astype(np.float32)                                              # (N, 10, L)


def compute_stats(F: np.ndarray, names: list[str]) -> dict:
    """정상 train 정규화 통계.  F: (N, C, L) → {"median", "scale"} (C,)"""
    flat = F.transpose(1, 0, 2).reshape(F.shape[1], -1)                     # (C, N·L)
    lo, hi = np.percentile(flat, WINSOR, axis=1)
    w = np.clip(flat, lo[:, None], hi[:, None])
    return {"features": names, "median": np.median(flat, axis=1).tolist(),
            "scale": np.maximum(w.std(axis=1), SCALE_FLOOR).tolist()}


def normalize(F: np.ndarray, stats: dict) -> np.ndarray:
    """z = clip((x − median) / scale, ±Z_CLIP).  (N, C, L) → (N, C, L)"""
    med = np.asarray(stats["median"], dtype=np.float32)[None, :, None]
    sc = np.asarray(stats["scale"], dtype=np.float32)[None, :, None]
    return np.clip((F - med) / sc, -Z_CLIP, Z_CLIP).astype(np.float32)


if __name__ == "__main__":
    # 스모크 테스트: 셀1 이 5 s 마다 1 mV 처지고 발열 → 마지막 윈도우 tdV 셀1 −11 mV, 나머지 +3.67 mV
    try:
        n = 60
        meta = pd.DataFrame({"source": "t", "SimulationID": 1, "WindowStart_s": 5.0 + WIN_HOP_S * np.arange(n)})
        S = np.zeros((n, 13), dtype=np.float32)
        S[:, 0:4] = 3.7 + 0.002 * np.arange(n)[:, None]
        S[:, 0] -= 0.0002 * np.arange(n)                                     # 1 s 마다 0.2 mV = 5 s 마다 1 mV
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
