"""
make_figures.py — README 용 그림 2 장 생성

    docs/ae_architecture.png      : AE 판정 구조 (센싱 → 1 s 윈도우 → 55 s 이력 → 라우터 → 전문가 AE → 판정)
    docs/ae_detection_example.png : fault3 위험 soft ISC run 1 개 — 고장 셀 전압 차이 · AE 이상도 · HW 보호 시점
Usage:
    python docs/make_figures.py      (예시 그림은 AI_AutoEncoder/data 의 fault3 요약이 있어야 함)
"""

import importlib.util
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                         # noqa: E402
from matplotlib import font_manager                     # noqa: E402
from matplotlib.patches import FancyBboxPatch           # noqa: E402
from matplotlib.ticker import FuncFormatter             # noqa: E402
import numpy as np                                      # noqa: E402

# ===== 경로 / 상수 =====
REPO: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_DIR: str = os.path.join(REPO, "AI_AutoEncoder")
OUT_DIR: str = os.path.join(REPO, "docs")
FONT_CANDIDATES: list[str] = ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]
INK, INK2, LINE, SURF = "#0b0b0b", "#52514e", "#c9c8c2", "#fcfcfb"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"      # 범주 색 (고정 순서)
CRIT, GOOD = "#d03b3b", "#0ca30c"                                            # 상태 색 (경보 / 정상)
EXAMPLE_RUN: int | None = None          # None = 주행 세션 · HW 보호 동작 · 고장 전 경보 0 인 ISC run 중 R 최대 (충전 세션은 밸런싱 스위칭으로 셀 전압이 매초 출렁여 그림이 읽기 어려움)
PRE_S: float = 180.0                    # 고장 전 표시 구간 [s]


def set_font() -> None:
    """한글 글꼴 등록 (없으면 기본 글꼴)."""
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            font_manager.fontManager.addfont(p)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=p).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False


def box(ax, x: float, y: float, w: float, h: float, title: str, body: str = "", color: str = BLUE, fill: str = "#ffffff") -> None:
    """둥근 상자 + 제목 + 설명."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.018",
                                linewidth=1.6, edgecolor=color, facecolor=fill))
    ax.text(x + w / 2, y + h - 0.035, title, ha="center", va="top", fontsize=11.5, fontweight="bold", color=INK)
    if body:
        ax.text(x + w / 2, y + h - 0.085, body, ha="center", va="top", fontsize=9.2, color=INK2, linespacing=1.45)


def arrow(ax, x0: float, y0: float, x1: float, y1: float) -> None:
    """화살표."""
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.4))


def architecture() -> str:
    """AE 판정 구조 그림."""
    fig, ax = plt.subplots(figsize=(13.5, 5.6), dpi=170)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    ax.set_xlim(0, 1.36); ax.set_ylim(0, 0.62); ax.axis("off")
    # 1) 입력
    box(ax, 0.01, 0.30, 0.17, 0.25, "BMS 센싱 (10 Hz)", "셀 전압 V1~V4\n셀 온도 T1~T4\n팩 전류 I\n(BQ76922 → STM32 → Pi)", INK2)
    arrow(ax, 0.185, 0.425, 0.215, 0.425)
    box(ax, 0.22, 0.30, 0.17, 0.25, "1 s 윈도우", "10 샘플 평균\n1 s 마다 판정", INK2)
    arrow(ax, 0.395, 0.425, 0.425, 0.425)
    box(ax, 0.43, 0.30, 0.19, 0.25, "55 s 흐름 이력", "5 s 간격 윈도우 12 개\n셀 관점 특징 9 × 12\n팩 관점 특징 10 × 12", INK2)
    arrow(ax, 0.625, 0.425, 0.655, 0.425)
    # 2) 라우터
    box(ax, 0.66, 0.30, 0.17, 0.25, "센싱 라우터", "1 s 평균 전류로 상태 판별\n충전 / 주행 / 휴지\n학습 없음 · 추가 센서 없음", YELLOW, "#fffaf0")
    # 3) 전문가
    ys = [0.44, 0.255, 0.07]
    for y, (name, col) in zip(ys, [("충전 전문가", BLUE), ("주행 전문가", AQUA), ("휴지 전문가", ORANGE)]):
        arrow(ax, 0.835, 0.425, 0.875, y + 0.07)
        box(ax, 0.88, y, 0.22, 0.14, name, "셀 AE (4 셀 공유) + 팩 AE", col)
    # 4) 판정
    for y in ys:
        arrow(ax, 1.105, y + 0.07, 1.145, 0.31)
    box(ax, 1.15, 0.19, 0.2, 0.24, "판정", "복원오차 / τ > 1 → 이상\n→ 이상 셀 지목\n+ 과충전 규칙 (> 4.205 V)", CRIT, "#fff5f5")
    # 하단 설명
    ax.text(0.01, 0.17, "학습: 정상 데이터만 (고장 데이터·가짜 이상 미사용)\n"
                        "τ = 경로별 정상 검증 데이터 최대 오차 × 1.2\n"
                        "온도 입력: 실제 회로 NTC 센서 특성 반영 학습", fontsize=9.5, color=INK2, va="top", linespacing=1.6)
    ax.text(0.01, 0.6, "AE 기반 AI-BMS 이상 탐지 구조", fontsize=14, fontweight="bold", color=INK, va="top")
    p = os.path.join(OUT_DIR, "ae_architecture.png")
    fig.savefig(p, bbox_inches="tight", facecolor=SURF); plt.close(fig)
    return p


def load_eval():
    """AI_AutoEncoder/4_eval/eval.py 모듈."""
    spec = importlib.util.spec_from_file_location("ae_eval", os.path.join(AI_DIR, "4_eval", "eval.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def detection_example() -> str | None:
    """fault3 ISC run 1 개: 고장 셀 전압 차이 + AE 이상도 + HW 보호 시점."""
    ev = load_eval()
    try:
        S, meta = ev.load("fault3")
    except RuntimeError as e:
        print(f"[건너뜀] 예시 그림: {e}")
        return None
    with open(os.path.join(ev.WEIGHTS_DIR, "threshold.json"), encoding="utf-8") as f:
        thr = json.load(f)["experts"]
    experts = {r: ev.Expert(os.path.join(ev.WEIGHTS_DIR, r), ev.get_device()) for r in ev.EXPERTS}
    isc = (meta.fault_type == "softisc").to_numpy() & np.isfinite(meta.trip_s.to_numpy())
    dall = ev.predict(S[isc], meta[isc].reset_index(drop=True), experts, thr)
    if EXAMPLE_RUN is None:                                                  # 선택 규칙: 주행 · HW 보호 동작 · 고장 전 경보 0 · R 최대
        ok = [(g.fault_mag.iloc[0], rid) for rid, g in dall.groupby("SimulationID")
              if g.kind.iloc[0] == "drive" and g[g.t_end <= g.fault_start_s].pred.sum() == 0]
        rid = max(ok)[1]
    else:
        rid = EXAMPLE_RUN
    d = dall[dall.SimulationID == rid].sort_values("WindowStart_s")
    d = d[d.t_end >= d.fault_start_s - PRE_S]
    sel_idx = d.index.to_numpy()
    m0 = d.iloc[0]
    onset, trip, k = float(m0.fault_start_s), float(m0.trip_s), int(m0.fault_cell)
    t = (d.t_end.to_numpy() - onset) / 60.0                                  # 고장 시작 기준 [분]
    Sx = S[isc][sel_idx]
    V = Sx[:, 0:4]
    dv = (V[:, k - 1] - np.delete(V, k - 1, axis=1).mean(axis=1)) * 1e3
    dv -= np.median(dv[t < 0])
    ratio = d.ratio.to_numpy()
    hit = np.where((t >= 0) & (d.pred.to_numpy() == 1))[0]
    t_ae = t[hit[0]] if len(hit) else np.nan
    t_hw = (trip - onset) / 60.0
    fig, axs = plt.subplots(2, 1, figsize=(10.5, 5.6), dpi=170, sharex=True, gridspec_kw={"height_ratios": [1, 1.1]})
    fig.patch.set_facecolor(SURF)
    for a in axs:
        a.set_facecolor(SURF)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            a.spines[s].set_color(LINE)
        a.tick_params(colors=INK2, labelsize=9)
        a.grid(axis="y", color=LINE, lw=0.6, alpha=0.6)
    axs[0].plot(t, dv, color=BLUE, lw=2)
    axs[0].set_ylabel(f"셀{k} 전압 차이 [mV]\n(나머지 셀 평균 대비)", color=INK2, fontsize=9.5)
    axs[1].plot(t, ratio, color=ORANGE, lw=2)
    axs[1].axhline(1.0, color=INK2, lw=1, ls="--")
    axs[1].text(t.min(), 1.15, "판정 경계 τ", color=INK2, fontsize=8.5, va="bottom")
    axs[1].set_yscale("log")
    axs[1].yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    axs[1].set_ylim(0.05, max(ratio.max() * 1.5, 2.0))
    axs[1].set_ylabel("AE 이상도 (오차 / τ)", color=INK2, fontsize=9.5)
    axs[1].set_xlabel("고장 시작 기준 시간 [분]", color=INK2, fontsize=9.5)
    for a in axs:
        a.axvline(0, color=INK2, lw=1, ls=":")
        if np.isfinite(t_ae):
            a.axvline(t_ae, color=CRIT, lw=1.6)
        a.axvline(t_hw, color=INK, lw=1.6, ls="--")
    y0 = axs[0].get_ylim()[1]
    axs[0].text(0, y0, " 내부단락 시작", color=INK2, fontsize=9, va="top")
    if np.isfinite(t_ae):
        axs[1].text(t_ae, axs[1].get_ylim()[1], f" AE 경보 (고장 후 {t_ae * 60:.0f} s)", color=CRIT, fontsize=9.5, va="top", fontweight="bold")
    axs[1].text(t_hw, axs[1].get_ylim()[1], f" HW 보호 (고장 후 {t_hw:.1f} 분)", color=INK, fontsize=9.5, va="top", fontweight="bold")
    fig.suptitle(f"위험 soft ISC 예시 — 셀{k}, R = {float(m0.fault_mag):.1f} Ω ({"충전" if m0.kind == "charge" else "주행"} 중, 시뮬레이션)", fontsize=12,
                 fontweight="bold", color=INK, x=0.07, ha="left")
    fig.tight_layout()
    p = os.path.join(OUT_DIR, "ae_detection_example.png")
    fig.savefig(p, bbox_inches="tight", facecolor=SURF); plt.close(fig)
    print(f"  예시 run {rid}: AE {t_ae * 60:.0f} s, HW {t_hw * 60:.0f} s 후")
    return p


if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.join(AI_DIR, "3_model"))
        set_font()
        print(" ", architecture())
        print(" ", detection_example())
    except Exception as e:
        print(f"[오류] 그림 생성 실패: {e}")
        raise
