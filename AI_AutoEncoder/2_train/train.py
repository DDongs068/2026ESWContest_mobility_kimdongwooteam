import json
import os
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

AI_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AI_DIR, "3_model"))

from features import cell_features, pack_features, compute_stats, normalize, CELL_FEATURES, PACK_FEATURES, \
    N_CELL_F, N_PACK_F, L_HIST
from model import FlowAE, ae_scores, set_seed, get_device
from router import routed_history, route_of

DATA_DIR: str = os.path.join(AI_DIR, "data")
OUT_DIR: str = os.path.join(AI_DIR, "5_weights", "AE_retrain")

SEED: int = 42
EPOCHS: int = 60
LR: float = 1e-3
WEIGHT_DECAY: float = 1e-4
PATIENCE: int = 15
BATCH_MIN: int = 64
BATCH_MAX: int = 512
TAU_MARGIN: float = 1.2


def load_normal() -> tuple[np.ndarray, pd.DataFrame]:
    try:
        S = np.load(os.path.join(DATA_DIR, "summary_normal.npz"))["S"]
        meta = pd.read_csv(os.path.join(DATA_DIR, "meta_normal.csv"))
    except OSError as e:
        raise RuntimeError(f"정상 요약 로드 실패 ({DATA_DIR}) — 1_preprocess/preprocess.py 먼저 실행: {e}") from e
    return routed_history(S, meta, reset=True), meta


def train_ae(ckpt: str, Z_tr: np.ndarray, scn_tr: np.ndarray, Z_va: np.ndarray, scn_va: np.ndarray,
             n_feat: int, device: torch.device) -> pd.DataFrame:
    model = FlowAE(n_feat).to(device)
    batch = int(np.clip(len(Z_tr) // 20, BATCH_MIN, BATCH_MAX))
    print(f"    params {sum(p.numel() for p in model.parameters()):,} | train {Z_tr.shape} | val {Z_va.shape} | batch {batch}")
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)
    cnt = pd.Series(scn_tr).map(pd.Series(scn_tr).value_counts()).to_numpy(dtype=np.float64)
    p = (1.0 / cnt) / (1.0 / cnt).sum()
    rng = np.random.default_rng(SEED)
    steps, best, bad, hist = max(len(Z_tr) // batch, 1), float("inf"), 0, []
    for ep in range(1, EPOCHS + 1):
        model.train()
        tot = 0.0
        for _ in range(steps):
            z = torch.from_numpy(Z_tr[rng.choice(len(Z_tr), batch, p=p)]).to(device)
            loss = F.mse_loss(model(z), z)
            if not torch.isfinite(loss):
                print(f"    [경고] loss={loss.item()} 배치 건너뜀 (z {z.min().item():.1f}~{z.max().item():.1f})")
                continue
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        s = ae_scores(model, Z_va, device)
        per = {k: float(s[scn_va == k].mean()) for k in np.unique(scn_va)}
        va = float(np.mean(list(per.values())))
        hist.append({"epoch": ep, "train": tot / steps, "val": va, **per})
        if va < best:
            best, bad = va, 0
            torch.save(model.state_dict(), ckpt + ".tmp")
            os.replace(ckpt + ".tmp", ckpt)
        else:
            bad += 1
        if ep % 20 == 0 or ep == 1:
            print(f"    [epoch {ep:3d}] train {tot / steps:.4f} | val {va:.4f}", flush=True)
        if bad >= PATIENCE:
            print(f"    early stopping (epoch {ep})")
            break
    print(f"    best val {best:.4f}")
    return pd.DataFrame(hist)


def load_best(ckpt: str, n_feat: int, device: torch.device) -> FlowAE:
    m = FlowAE(n_feat)
    m.load_state_dict(torch.load(ckpt, map_location="cpu"))
    m = m.to(device)
    m.eval()
    return m


def train_expert(out_dir: str, H_tr: np.ndarray, scn_tr: np.ndarray, H_va: np.ndarray, scn_va: np.ndarray,
                 device: torch.device) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    Fc_tr = cell_features(H_tr).reshape(-1, N_CELL_F, L_HIST)
    Fp_tr = pack_features(H_tr)
    st_c, st_p = compute_stats(Fc_tr, CELL_FEATURES), compute_stats(Fp_tr, PACK_FEATURES)
    for n, st in (("cell", st_c), ("pack", st_p)):
        with open(os.path.join(out_dir, f"stats_{n}.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    Zc_va = normalize(cell_features(H_va).reshape(-1, N_CELL_F, L_HIST), st_c)
    Zp_va = normalize(pack_features(H_va), st_p)
    for name, Z_tr, s_tr, Z_va, s_va, nf in [
            ("cell", normalize(Fc_tr, st_c), np.repeat(scn_tr, 4), Zc_va, np.repeat(scn_va, 4), N_CELL_F),
            ("pack", normalize(Fp_tr, st_p), scn_tr, Zp_va, scn_va, N_PACK_F)]:
        print(f"   [{name}]")
        train_ae(os.path.join(out_dir, f"best_{name}_ae.pt"), Z_tr, s_tr, Z_va, s_va, nf, device).to_csv(
            os.path.join(out_dir, f"history_{name}.csv"), index=False)
    s_cell = ae_scores(load_best(os.path.join(out_dir, "best_cell_ae.pt"), N_CELL_F, device), Zc_va, device
                       ).reshape(-1, 4).max(axis=1)
    s_pack = ae_scores(load_best(os.path.join(out_dir, "best_pack_ae.pt"), N_PACK_F, device), Zp_va, device)
    return {"cell": float(s_cell.max() * TAU_MARGIN), "pack": float(s_pack.max() * TAU_MARGIN),
            "n_train": int(len(H_tr)), "n_val": int(len(H_va)),
            "cell_val_median": float(np.median(s_cell)), "pack_val_median": float(np.median(s_pack))}


def main() -> None:
    set_seed(SEED)
    device = get_device()
    if os.path.exists(os.path.join(OUT_DIR, "threshold.json")):
        raise FileExistsError(f"이미 존재: {OUT_DIR}/threshold.json (평가에 쓴 가중치 보호 — OUT_DIR 을 바꿔서 재학습)")
    hp = {"SEED": SEED, "EPOCHS": EPOCHS, "LR": LR, "WEIGHT_DECAY": WEIGHT_DECAY, "PATIENCE": PATIENCE,
          "BATCH": f"clip(N/20, {BATCH_MIN}, {BATCH_MAX})", "TAU_MARGIN": TAU_MARGIN, "L_HIST": L_HIST,
          "train_data": "정상만 (가짜 이상 미사용)"}
    print(f"[1/2] 정상 데이터 (device {device})\n  hparams: {json.dumps(hp, ensure_ascii=False)}")
    H, meta = load_normal()
    tr, va = (meta["split"] == "train").to_numpy(), (meta["split"] == "val").to_numpy()
    scn, route = meta["scenario"].to_numpy(), route_of(H)
    print(f"  H {H.shape}  train {tr.sum()}  val {va.sum()}")
    print("[2/2] 전문가 학습")
    thr = {"variant": "AE", "train_data_note": "정상 데이터만 학습 + 온도 입력에 실측 NTC 센서 특성 반영 (1_preprocess/preprocess.py)", "window_samples": 10, "hist_step": 5, "n_expert": 3, "margin": TAU_MARGIN, "reset": True, "hparams": hp, "experts": {}}
    for r in sorted(np.unique(route)):
        mtr, mva = tr & (route == r), va & (route == r)
        print(f"  [{r}] train {mtr.sum()}  val {mva.sum()}")
        set_seed(SEED)
        e = train_expert(os.path.join(OUT_DIR, r), H[mtr], scn[mtr], H[mva], scn[mva], device)
        thr["experts"][r] = e
        print(f"   τ cell {e['cell']:.3f} (val 중앙값의 {e['cell'] / e['cell_val_median']:.1f}배)  "
              f"τ pack {e['pack']:.3f} (val 중앙값의 {e['pack'] / e['pack_val_median']:.1f}배)")
    with open(os.path.join(OUT_DIR, "threshold.json"), "w", encoding="utf-8") as f:
        json.dump(thr, f, indent=2, ensure_ascii=False)
    print(f"\n완료 → {OUT_DIR}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[오류] 학습 실패: {e}")
        raise
