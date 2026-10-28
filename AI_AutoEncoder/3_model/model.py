import json
import os
import random

import numpy as np
import torch
import torch.nn as nn

from features import cell_features, pack_features, normalize, N_CELL_F, N_PACK_F, L_HIST

WEIGHTS_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "5_weights", "AE")
HIDDEN: int = 128
LATENT: int = 12
SPEC_V: float = 4.205


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def get_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")


class FlowAE(nn.Module):

    def __init__(self, n_feat: int, L: int = L_HIST, hidden: int = HIDDEN, latent: int = LATENT) -> None:
        super().__init__()
        self.n_feat, self.L = n_feat, L
        flat = n_feat * L
        self.enc = nn.Sequential(nn.Flatten(), nn.Linear(flat, hidden), nn.SiLU(),
                                 nn.Linear(hidden, 64), nn.SiLU(), nn.Linear(64, latent))
        self.dec = nn.Sequential(nn.Linear(latent, 64), nn.SiLU(), nn.Linear(64, hidden), nn.SiLU(),
                                 nn.Linear(hidden, flat))

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        return self.dec(self.enc(z)).view(-1, self.n_feat, self.L)


@torch.no_grad()
def ae_reconstruct(model: nn.Module, Z: np.ndarray, device: torch.device, batch: int = 8192) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(Z), batch):
        z = torch.from_numpy(Z[i:i + batch]).to(device)
        out.append(model(z).cpu().numpy())
    return np.concatenate(out) if out else np.zeros_like(Z)


@torch.no_grad()
def ae_scores(model: nn.Module, Z: np.ndarray, device: torch.device, batch: int = 8192) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(Z), batch):
        z = torch.from_numpy(Z[i:i + batch]).to(device)
        out.append((model(z) - z).pow(2).mean(dim=(1, 2)).cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


class Expert:

    def __init__(self, exp_dir: str, device: torch.device) -> None:
        self.dir, self.device = exp_dir, device
        self.stats, self.models = {}, {}
        for name, nf in (("cell", N_CELL_F), ("pack", N_PACK_F)):
            try:
                with open(os.path.join(exp_dir, f"stats_{name}.json"), encoding="utf-8") as f:
                    self.stats[name] = json.load(f)
                m = FlowAE(nf)
                m.load_state_dict(torch.load(os.path.join(exp_dir, f"best_{name}_ae.pt"), map_location="cpu"))
            except (OSError, RuntimeError, json.JSONDecodeError) as e:
                raise RuntimeError(f"전문가 로드 실패 ({exp_dir}/{name}): {e}") from e
            m = m.to(device)
            m.eval()
            self.models[name] = m

    def inputs(self, H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Zc = normalize(cell_features(H).reshape(-1, N_CELL_F, L_HIST), self.stats["cell"])
        Zp = normalize(pack_features(H), self.stats["pack"])
        return Zc, Zp

    def scores(self, H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Zc, Zp = self.inputs(H)
        return (ae_scores(self.models["cell"], Zc, self.device).reshape(-1, 4),
                ae_scores(self.models["pack"], Zp, self.device))


def spec_overcharge(Vcell: np.ndarray) -> np.ndarray:
    return np.median(Vcell.max(axis=1), axis=1) > SPEC_V


if __name__ == "__main__":
    try:
        dev = torch.device("cpu")
        for nf in (N_CELL_F, N_PACK_F):
            m = FlowAE(nf)
            Z = np.random.randn(5, nf, L_HIST).astype(np.float32)
            print(f"  FlowAE({nf}) params {sum(p.numel() for p in m.parameters()):,} | "
                  f"recon {ae_reconstruct(m, Z, dev).shape} | score {ae_scores(m, Z, dev).shape}")
        V = np.full((2, 4, 10), 4.10, dtype=np.float32)
        V[1, 2] = 4.25
        print(f"  사양 규칙: {spec_overcharge(V).tolist()} (기대 [False, True])")
    except Exception as e:
        print(f"[오류] model 스모크 테스트 실패: {e}")
        raise
