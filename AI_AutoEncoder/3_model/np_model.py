"""
np_model.py — torch 없이 numpy 만으로 AE 추론 (Raspberry Pi 5 배포용, model.Expert 와 같은 인터페이스)

FlowAE (MLP) 순전파를 numpy 로 그대로 계산:
    flatten → Linear → SiLU → Linear → SiLU → Linear(latent) → Linear → SiLU → Linear → SiLU → Linear → reshape
    SiLU(x) = x · σ(x) = x / (1 + e^{−x})
가중치: export_weights() 가 best_{cell,pack}_ae.pt → weights_{cell,pack}.npz 로 변환 (Mac 에서 1회, torch 필요)

Classes / Functions:
    NpFlowAE(npz_path)        : (N, C, L) → (N, C, L)
    NpExpert(exp_dir)         : model.Expert 와 같은 inputs / scores
    export_weights(model_dir) : .pt → .npz (전문가 폴더마다)

Usage:
    ex = NpExpert(os.path.join(WEIGHTS_DIR, "dis")); s_cell_i, s_pack = ex.scores(H)   # WEIGHTS_DIR = 5_weights/AE
"""

import json
import os

import numpy as np

from features import cell_features, pack_features, normalize, N_CELL_F, N_PACK_F, L_HIST

# ===== 경로 / 상수 =====
AI_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR: str = os.path.join(AI_DIR, "5_weights", "AE")
DATA_DIR: str = os.path.join(AI_DIR, "data")
ACT_AFTER: tuple[int, ...] = (0, 1, 3, 4)          # 6 개 Linear 중 SiLU 가 뒤따르는 층 (latent·출력층 제외)


def silu(x: np.ndarray) -> np.ndarray:
    """SiLU(x) = x / (1 + e^{−x}) (overflow 방지: clip)."""
    return x / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


class NpFlowAE:
    """numpy MLP AE.  (N, C, L) → (N, C, L)"""

    def __init__(self, npz_path: str) -> None:
        try:
            d = np.load(npz_path)
        except OSError as e:
            raise RuntimeError(f"가중치 로드 실패: {npz_path}: {e}") from e
        n = int(d["n_layers"])
        self.W = [d[f"W{i}"].astype(np.float32) for i in range(n)]     # (out, in)
        self.b = [d[f"b{i}"].astype(np.float32) for i in range(n)]
        self.n_feat, self.L = int(d["n_feat"]), int(d["L"])

    def __call__(self, z: np.ndarray) -> np.ndarray:
        """(N, C, L) → (N, C·L) → … → (N, C, L)"""
        x = z.reshape(len(z), -1)
        for i, (W, b) in enumerate(zip(self.W, self.b)):
            x = x @ W.T + b
            if i in ACT_AFTER:
                x = silu(x)
        return x.reshape(-1, self.n_feat, self.L)


def np_scores(model: NpFlowAE, Z: np.ndarray, batch: int = 8192) -> np.ndarray:
    """샘플별 복원오차 mean (ẑ − z)².  (N, C, L) → (N,)"""
    out = [((model(Z[i:i + batch]) - Z[i:i + batch]) ** 2).mean(axis=(1, 2)) for i in range(0, len(Z), batch)]
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


class NpExpert:
    """경로 1개 = 셀 공용 AE + 팩 AE + 정규화 통계 (model.Expert 와 같은 인터페이스)."""

    def __init__(self, exp_dir: str, device=None) -> None:
        self.stats, self.models = {}, {}
        for name in ("cell", "pack"):
            try:
                with open(os.path.join(exp_dir, f"stats_{name}.json"), encoding="utf-8") as f:
                    self.stats[name] = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"정규화 통계 로드 실패 ({exp_dir}/{name}): {e}") from e
            self.models[name] = NpFlowAE(os.path.join(exp_dir, f"weights_{name}.npz"))

    def inputs(self, H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """H (N, L, 13) → Zc (N·4, 9, L), Zp (N, 10, L)"""
        return (normalize(cell_features(H).reshape(-1, N_CELL_F, L_HIST), self.stats["cell"]),
                normalize(pack_features(H), self.stats["pack"]))

    def scores(self, H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """H (N, L, 13) → 셀별 (N, 4), 팩 (N,)"""
        Zc, Zp = self.inputs(H)
        return np_scores(self.models["cell"], Zc).reshape(-1, 4), np_scores(self.models["pack"], Zp)


def export_weights(model_dir: str) -> list[str]:
    """<model_dir>/<경로>/best_*_ae.pt → weights_*.npz (torch 필요, 학습 PC 에서 1회)."""
    import torch
    out = []
    for r in sorted(os.listdir(model_dir)):
        d = os.path.join(model_dir, r)
        if not os.path.isdir(d):
            continue
        for name, nf in (("cell", N_CELL_F), ("pack", N_PACK_F)):
            sd = torch.load(os.path.join(d, f"best_{name}_ae.pt"), map_location="cpu")
            keys = sorted({k.rsplit(".", 1)[0] for k in sd}, key=lambda k: (k.split(".")[0] != "enc", int(k.split(".")[1])))
            arrs = {"n_layers": len(keys), "n_feat": nf, "L": L_HIST}
            for i, k in enumerate(keys):
                arrs[f"W{i}"], arrs[f"b{i}"] = sd[f"{k}.weight"].numpy(), sd[f"{k}.bias"].numpy()
            p = os.path.join(d, f"weights_{name}.npz")
            with open(p + ".tmp", "wb") as f:
                np.savez(f, **arrs)
            os.replace(p + ".tmp", p)
            out.append(p)
    return out


if __name__ == "__main__":
    # 변환 + torch 판과 일치 검증: fault3 일부 윈도우로 셀·팩 점수 비교 (1_preprocess 실행 후)
    try:
        import torch
        from model import Expert
        from router import routed_history
        import pandas as pd
        MODEL_DIR = WEIGHTS_DIR
        print("  변환:", [os.path.relpath(p, MODEL_DIR) for p in export_weights(MODEL_DIR)])
        S = np.load(os.path.join(DATA_DIR, "summary_fault3.npz"))["S"][:3000]
        meta = pd.read_csv(os.path.join(DATA_DIR, "meta_fault3.csv")).iloc[:3000]
        H = routed_history(S, meta)
        for r in ("chg", "dis", "rest"):
            a = Expert(os.path.join(MODEL_DIR, r), torch.device("cpu")).scores(H[:500])
            b = NpExpert(os.path.join(MODEL_DIR, r)).scores(H[:500])
            print(f"  [{r}] 셀 점수 최대 상대오차 {np.max(np.abs(a[0] - b[0]) / (np.abs(a[0]) + 1e-9)):.2e}  "
                  f"팩 {np.max(np.abs(a[1] - b[1]) / (np.abs(a[1]) + 1e-9)):.2e}")
    except Exception as e:
        print(f"[오류] np_model 검증 실패: {e}")
        raise
