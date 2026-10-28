import csv
import json
import os
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np

APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
AI_DIR: str = os.path.dirname(os.path.dirname(APP_DIR))
FINAL_DIR: str = os.path.join(AI_DIR, "3_model")
MODEL_NAME: str = "AE"
MODEL_DIR: str = os.path.join(AI_DIR, "5_weights", "AE")
try:
    with open("/proc/device-tree/model", encoding="utf-8", errors="ignore") as _f:
        ON_RASPBERRY_PI: bool = "Raspberry Pi" in _f.read()
except OSError:
    ON_RASPBERRY_PI = False
USE_BAL: bool = False
HTML_PATH: str = os.path.join(APP_DIR, "monitor.html")
BUFFER_HTML: str = os.path.join(APP_DIR, "buffer.html")
LOG_DIR: str = os.path.join(APP_DIR, "runtime", "monitor_logs")
WIN_DIR: str = os.path.join(APP_DIR, "runtime", "windows")
SPEC_V: float = 4.205

sys.path.insert(0, FINAL_DIR)
from router import EXPERTS, I_REST
from features import L_HIST
torch = None
from np_model import NpExpert as Expert
BACKEND: str = "numpy"

HOST: str = "0.0.0.0"
PORT: int = 8050
WINDOW: int = 10
HOP: int = 10
HIST_SPACING: int = 50
GRID: int = min(WINDOW, HIST_SPACING)
LONG_BUF: int = WINDOW + (L_HIST - 1) * HIST_SPACING
SAMPLE_DT: float = 0.1
HIST_SAMPLES: int = 3000
HIST_WINDOWS: int = 360
MAX_PLOT_POINTS: int = 600
MA_K: int = 10
COLS: list[str] = ["time_s", "CellV1", "CellV2", "CellV3", "CellV4", "PackI", "Temp1", "Temp2", "Temp3", "Temp4",
                   "Bal1", "Bal2", "Bal3", "Bal4"]
LEGACY: dict[str, tuple[float, float]] = {"COV": (4.25, 0.5), "CUV": (2.80, 1.0), "OCD": (6.0, 0.1), "OT": (60.0, 1.0)}
ROUTE_KO: dict[str, str] = {"chg": "충전", "dis": "주행·방전", "rest": "휴지"}
PROTO: str = "v2"
V2_FIELDS: list[str] = ["seq", "ms", "valid", "V1", "V2", "V3", "V4", "T1", "T2", "T3", "T4", "I", "STACK", "PACK", "LD"]
UDP_IN_PORT: int = 5005
RELAY_IN_PORT: int = 5008
RX_LOG_DIR: str = os.path.join(APP_DIR, "runtime", "rx_log")
PCB_ADDR: tuple[str, int] = ("127.0.0.1", 5006)
PCB_CTRL_ADDR: tuple[str, int] = ("127.0.0.1", 5007)
SRC_KO: dict[str, str] = {"PCB": "PCB 실측", "PCB-REPLAY": "PCB 재생 (시뮬레이션)",
                          "PCB-EMU": "PCB 대역 (시뮬레이션 재생)", "PCB-EMU-NORMAL": "PCB 대역 (정상 시험 데이터)",
                          "SIM": "시뮬레이션 직접", "HTTP": "직접 입력",
                          "MAC": "Mac 시뮬레이션 (비정상, USB → 엑셀 버퍼)"}
MODE_SOURCES: dict[str, set[str]] = {"normal": {"PCB", "PCB-EMU-NORMAL", "HTTP"},
                                     "abnormal": {"MAC", "PCB-EMU", "PCB-REPLAY", "SIM", "HTTP"}}


class Legacy:

    def __init__(self) -> None:
        self.timer = {k: 0.0 for k in LEGACY}
        self.tripped: dict[str, float] = {}

    def update(self, v: np.ndarray, i: float, t: np.ndarray, now: float) -> None:
        cond = {"COV": v.max() > LEGACY["COV"][0], "CUV": v.min() < LEGACY["CUV"][0],
                "OCD": -i > LEGACY["OCD"][0], "OT": t.max() > LEGACY["OT"][0]}
        for k, c in cond.items():
            self.timer[k] = self.timer[k] + SAMPLE_DT if c else 0.0
            if self.timer[k] >= LEGACY[k][1] and k not in self.tripped:
                self.tripped[k] = now


class MonitorState:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.accepting = True
        self.mode = "abnormal"
        self.n_relay = 0
        self.n_to_pcb = 0
        self.n_from_pcb = 0
        self.n_dropped = 0
        self.last_pcb_wall = 0.0
        self.relay_t: dict[int, float] = {}
        self.rtt_ms: deque = deque(maxlen=200)
        self.seq_last: dict[str, int] = {}
        self.seq_lost: dict[str, int] = {}
        self.rx_file = None
        self.rx_name = ""
        self.n_invalid = 0
        self.n_ignored: dict[str, int] = {}
        self.reset()

    def reset(self) -> None:
        self.samples: deque = deque(maxlen=HIST_SAMPLES)
        self.buf: deque = deque(maxlen=WINDOW)
        self.raw_buf: deque = deque(maxlen=WINDOW)
        self.long: deque = deque(maxlen=LONG_BUF)
        self.hist_routes: list = []
        self.source = ""
        self.close_rx_log()
        self.legacy_src = "Pi 계산"
        self.last_decision: dict | None = None
        self.last_window_file = ""
        self.since_hop = 0
        self.n_received = 0
        self.last_rx_wall = 0.0
        self.rx_rate: deque = deque(maxlen=50)
        self.seg_windows: list = []
        self.seg_state = ""
        self.win: deque = deque(maxlen=HIST_WINDOWS)
        self.status = "WAITING"
        self.alarm_t0 = None
        self.fault_cell = 0
        self.legacy = Legacy()
        self.n_windows = 0
        self.n_alert = 0
        self.last_infer_ms = 0.0
        self.messages: deque = deque(maxlen=6)


    def close_rx_log(self) -> None:
        f = getattr(self, "rx_file", None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        self.rx_file = None

    def rx_log(self, src: str, raw: list) -> None:
        try:
            if self.rx_file is None:
                os.makedirs(RX_LOG_DIR, exist_ok=True)
                self.rx_name = f"rx_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{src}.csv"
                self.rx_file = open(os.path.join(RX_LOG_DIR, self.rx_name), "w", encoding="utf-8")
                self.rx_file.write("pi_wall,src," + ",".join(V2_FIELDS) + "\n")
            self.rx_file.write(f"{time.time():.3f},{src}," + ",".join(f"{v:.6g}" for v in raw) + "\n")
        except OSError as e:
            self.messages.append(f"수신 기록 실패: {e}")


def internal_to_v2(seq: int | None, r: list) -> list:
    stack = float(sum(r[1:5]))
    return [-1 if seq is None else seq, round(r[0] * 1000), 1, *r[1:5], *r[6:10], r[5], stack, stack, stack]


def v2_to_internal(raw: list) -> list:
    return [raw[1] / 1000.0, *raw[3:7], raw[11], *raw[7:11], 0.0, 0.0, 0.0, 0.0]


def window_summary(rows: np.ndarray) -> tuple[np.ndarray, float]:
    V, I, T, B = rows[:, 0:4], rows[:, 4], rows[:, 5:9], rows[:, 9:13]
    if not USE_BAL:
        B = np.zeros_like(B)
    S = np.concatenate([V.mean(0), T.mean(0), B.mean(0), [I.mean()]]).astype(np.float32)
    return S, float(np.median(V.max(1)))


def window_state(I_mean: float) -> str:
    return "chg" if I_mean > I_REST else ("dis" if I_mean < -I_REST else "rest")


def history(seg: list) -> np.ndarray:
    w = seg[-L_HIST:]
    w = [w[0]] * (L_HIST - len(w)) + w
    return np.stack(w)[None].astype(np.float32)


def history_windows(long_rows: np.ndarray) -> tuple[list, list, float, str]:
    n = len(long_rows)
    seg, states = [], []
    if n < WINDOW:
        return seg, states, 0.0, ""
    S0, vmax_med = window_summary(long_rows[n - WINDOW:n])
    route = window_state(float(S0[12]))
    oldest_ok = n - WINDOW
    e = n - GRID
    while e - WINDOW >= 0:
        if window_state(float(long_rows[e - WINDOW:e, 4].mean())) != route:
            break
        oldest_ok = e - WINDOW
        e -= GRID
    first_S, _ = window_summary(long_rows[oldest_ok:oldest_ok + WINDOW])
    n_real = 0
    for k in range(L_HIST):
        e = n - k * HIST_SPACING
        if e - WINDOW < oldest_ok:
            seg.append(first_S)
            continue
        S, _ = window_summary(long_rows[e - WINDOW:e])
        seg.append(S)
        n_real += 1
    states = [route] * n_real
    return seg[::-1], states[::-1], vmax_med, route


def run_inference(st: MonitorState, experts: dict, thr: dict, t_end: float) -> None:
    t0 = time.perf_counter()
    full = np.array(st.buf, dtype=np.float64)
    long_rows = np.array(st.long, dtype=np.float32)[:, 1:]
    seg, st.hist_routes, vmax_med, route = history_windows(long_rows)
    if route != st.seg_state:
        if st.seg_state:
            st.messages.append(f"{t_end:7.1f} s  상태 전환 → {ROUTE_KO[route]} (이력 리셋)")
        st.seg_state = route
    st.seg_windows = seg
    sc, sp = experts[route].scores(history(seg))
    tc, tp = thr[route]["cell"], thr[route]["pack"]
    cell_ratio = (sc[0] / tc).astype(float)
    pack_ratio = float(sp[0] / tp)
    ae = bool(cell_ratio.max() > 1 or pack_ratio > 1)
    spec = vmax_med > SPEC_V
    pred = ae or spec
    st.last_infer_ms = (time.perf_counter() - t0) * 1000
    st.n_windows += 1
    st.n_alert += int(pred)
    guess = int(np.argmax(cell_ratio)) + 1
    st.win.append({"t": t_end, "route": route, "cell": cell_ratio.round(4).tolist(), "pack": round(pack_ratio, 4),
                   "pred": int(pred), "spec": int(spec), "hist": len(st.seg_windows)})
    if pred and st.status != "ABNORMAL":
        st.alarm_t0 = t_end
        st.fault_cell = guess
        why = f"셀 {guess} 이상도 {cell_ratio.max() * 100:.0f}%" if ae else f"최대셀 전압 {vmax_med:.3f} V > {SPEC_V} V"
        st.messages.append(f"{t_end:7.1f} s  ABNORMAL 진입 — {why}")
        log_event(t_end, route, cell_ratio, pack_ratio, guess, spec)
    elif pred:
        st.fault_cell = guess
    if not pred and st.status == "ABNORMAL":
        st.messages.append(f"{t_end:7.1f} s  NORMAL 복귀")
    st.status = "ABNORMAL" if pred else "NORMAL"
    log_window(t_end, route, cell_ratio, pack_ratio, pred, spec)
    st.last_decision = {"n": st.n_windows, "t": t_end, "status": st.status, "route": route, "fault_cell": guess if pred else 0,
                        "max_pct": round(float(max(cell_ratio.max(), pack_ratio)) * 100, 1), "spec": int(spec)}
    st.last_window_file = save_window_csv(list(st.raw_buf), st.n_windows, t_end, st.source, route, cell_ratio, pack_ratio, pred)


def save_window_csv(raws: list, n: int, t_end: float, src: str, route: str, cr: np.ndarray, pr: float, pred: bool) -> str:
    try:
        os.makedirs(WIN_DIR, exist_ok=True)
        name = f"window_{n:05d}_t{t_end:08.1f}_{'ABNORMAL' if pred else 'NORMAL'}.csv"
        with open(os.path.join(WIN_DIR, name), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["row"] + V2_FIELDS)
            for i, r in enumerate(raws, 1):
                w.writerow([i] + [f"{v:.6g}" for v in r])
        idx = os.path.join(WIN_DIR, "windows_index.csv")
        new = not os.path.exists(idx)
        with open(idx, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["n", "t_end_s", "source", "route", "cell1_pct", "cell2_pct", "cell3_pct", "cell4_pct", "pack_pct", "decision", "file"])
            w.writerow([n, f"{t_end:.1f}", src, route, *[round(c * 100, 1) for c in cr], round(pr * 100, 1),
                        "ABNORMAL" if pred else "NORMAL", name])
        return name
    except OSError as e:
        print(f"[경고] 윈도우 CSV 저장 실패: {e}")
        return ""


def log_event(t: float, route: str, cr: np.ndarray, pr: float, guess: int, spec: bool) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, "alert_log.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["wall", "t_s", "route", "cell1", "cell2", "cell3", "cell4", "pack", "fault_cell", "spec"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), f"{t:.1f}", route, *np.round(cr, 3), round(pr, 3), guess, int(spec)])
    except OSError as e:
        print(f"[경고] 경보 로그 실패: {e}")


def log_window(t: float, route: str, cr: np.ndarray, pr: float, pred: bool, spec: bool) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        p = os.path.join(LOG_DIR, "window_log.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["t_s", "route", "cell1", "cell2", "cell3", "cell4", "pack", "pred", "spec"])
            w.writerow([f"{t:.1f}", route, *np.round(cr, 4), round(pr, 4), int(pred), int(spec)])
    except OSError as e:
        print(f"[경고] 윈도우 로그 실패: {e}")


def ingest(st: MonitorState, rows: list, experts: dict, thr: dict, src: str = "HTTP", flags: list | None = None,
           seqs: list | None = None, raws: list | None = None) -> int:
    n = 0
    with st.lock:
        if not st.accepting:
            st.n_dropped += len(rows)
            return 0
        if src not in MODE_SOURCES.get(st.mode, set()):
            st.n_ignored[src] = st.n_ignored.get(src, 0) + len(rows)
            return 0
        for k, r in enumerate(rows):
            if len(r) != len(COLS):
                continue
            if st.source and src != st.source:
                old = st.source
                st.reset()
                st.messages.append(f"출처 변경 {SRC_KO.get(old, old)} → {SRC_KO.get(src, src)} : 버퍼 초기화")
            st.source = src
            seq = seqs[k] if seqs is not None else None
            if seq is not None:
                last = st.seq_last.get(src)
                if last is not None and seq > last + 1:
                    st.seq_lost[src] = st.seq_lost.get(src, 0) + (seq - last - 1)
                st.seq_last[src] = seq
            raw = raws[k] if raws is not None else internal_to_v2(seq, r)
            st.rx_log(src, raw)
            t, x = float(r[0]), np.asarray(r[1:], dtype=np.float32)
            if st.samples and t < st.samples[-1][0] - 1e-6:
                st.reset()
                st.source = src
                st.messages.append("시간 역행 감지 → 버퍼 초기화")
            if st.rx_file is not None and k % 10 == 9:
                st.rx_file.flush()
            st.samples.append((t, x))
            row = np.concatenate([[t], x])
            st.buf.append(row)
            st.raw_buf.append(raw)
            st.long.append(row)
            st.since_hop += 1
            st.n_received += 1
            if flags is not None and flags[k] is not None:
                st.legacy_src = "PCB 대역 (STM32 역할)" if src.startswith("PCB-EMU") else "PCB (STM32)"
                for name, f in zip(("COV", "CUV", "OCD", "OT"), flags[k]):
                    if f and name not in st.legacy.tripped:
                        st.legacy.tripped[name] = t
            else:
                st.legacy.update(x[0:4], float(x[4]), x[5:9], t)
            n += 1
            if len(st.buf) == WINDOW and st.since_hop >= HOP:
                st.since_hop = 0
                run_inference(st, experts, thr, t)
        st.last_rx_wall = time.time()
        st.rx_rate.append((st.last_rx_wall, st.n_received))
    return n


def parse_udp(text: str) -> dict[str, tuple[list, list, list]]:
    out: dict[str, tuple[list, list, list, list]] = {}
    for line in text.strip().splitlines():
        p = line.strip().split(",")
        try:
            if len(p) == 15 and p[0].lstrip("-").isdigit():
                raw = [float(v) for v in p]
                raw[0], raw[1], raw[2] = int(p[0]), int(float(p[1])), int(float(p[2]))
                rows, flags, seqs, raws = out.setdefault("V2", ([], [], [], []))
                rows.append(v2_to_internal(raw)); flags.append(None); seqs.append(raw[0]); raws.append(raw)
                continue
            if len(p) in (16, 20):
                seq, vals, fl = int(p[1]), p[2:16], p[16:20]
            elif len(p) in (15, 19):
                seq, vals, fl = None, p[1:15], p[15:19]
            else:
                continue
            row = [float(v) for v in vals]
            flv = [int(float(v)) for v in fl] if fl else None
        except ValueError:
            continue
        rows, flags, seqs, raws = out.setdefault(p[0], ([], [], [], []))
        rows.append(row)
        flags.append(flv)
        seqs.append(seq)
        raws.append(internal_to_v2(seq, row))
    return out


def udp_loop(st: MonitorState, experts: dict, thr: dict) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, UDP_IN_PORT))
    except OSError as e:
        print(f"[오류] UDP {UDP_IN_PORT} 열기 실패: {e}")
        return
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except OSError:
            continue
        now = time.time()
        for src, (rows, flags, seqs, raws) in parse_udp(data.decode("utf-8", "ignore")).items():
            if src == "V2":
                emu = addr[0] == "127.0.0.1"
                src = ("PCB-EMU-NORMAL" if emu else "PCB") if st.mode == "normal" else ("PCB-EMU" if emu else "PCB-REPLAY")
            ok = [k for k, r in enumerate(raws) if r[2] == 1]
            with st.lock:
                st.n_invalid += len(raws) - len(ok)
            ingest(st, [rows[k] for k in ok], experts, thr, src=src, flags=[flags[k] for k in ok],
                   seqs=[seqs[k] for k in ok], raws=[raws[k] for k in ok])
            with st.lock:
                st.n_from_pcb += len(rows)
                st.last_pcb_wall = now
                for q in seqs:
                    t_sent = st.relay_t.pop(q, None) if q is not None else None
                    if t_sent is not None:
                        st.rtt_ms.append((now - t_sent) * 1000)


def relay_loop(st: MonitorState) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((HOST, RELAY_IN_PORT))
    except OSError as e:
        print(f"[오류] UDP {RELAY_IN_PORT} 열기 실패: {e}")
        return
    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except OSError:
            continue
        lines = [ln for ln in data.decode("utf-8", "ignore").strip().splitlines() if ln.count(",") >= 14]
        now = time.time()
        with st.lock:
            st.n_relay += len(lines)
            if not st.accepting:
                st.n_dropped += len(lines)
                continue
            for ln in lines:
                f = ln.split(",")
                try:
                    st.relay_t[int(f[0]) if f[0].lstrip("-").isdigit() else int(f[1])] = now
                except (ValueError, IndexError):
                    pass
            if len(st.relay_t) > 5000:
                st.relay_t = dict(list(st.relay_t.items())[-1000:])
        try:
            out.sendto(data, PCB_ADDR)
            with st.lock:
                st.n_to_pcb += len(lines)
        except OSError as e:
            with st.lock:
                st.messages.append(f"PCB 중계 실패: {e}")


def relay_to_pcb(st: MonitorState, rows: list, sock: socket.socket) -> int:
    with st.lock:
        st.n_relay += len(rows)
        if not st.accepting:
            return 0
    lines = ["SIM," + ",".join(f"{v:.5g}" if j else f"{v:.2f}" for j, v in enumerate(r)) for r in rows if len(r) == len(COLS)]
    try:
        sock.sendto("\n".join(lines).encode(), PCB_ADDR)
    except OSError as e:
        with st.lock:
            st.messages.append(f"PCB 중계 실패: {e}")
        return 0
    with st.lock:
        st.n_to_pcb += len(lines)
    return len(lines)


def control(st: MonitorState, body: dict, sock: socket.socket) -> dict:
    a, mode = body.get("action", ""), body.get("mode")
    with st.lock:
        if mode in ("normal", "abnormal") and mode != st.mode:
            st.mode = mode
            st.reset()
            st.messages.append(f"모드 변경 → {'정상 (PCB 실측)' if mode == 'normal' else '비정상 (시뮬레이션 재생)'} : 버퍼 초기화")
        if a == "start":
            st.accepting = True
        elif a == "stop":
            st.accepting = False
        elif a == "reset":
            st.reset()
            st.n_relay = st.n_to_pcb = st.n_from_pcb = st.n_dropped = st.n_invalid = 0
            st.n_ignored = {}
            st.seq_last, st.seq_lost, st.relay_t = {}, {}, {}
            st.rtt_ms.clear()
        msg = f"{a} ({st.mode})"
        st.messages.append(f"제어: {msg}")
        cur_mode = st.mode
    ctrl = {"start": f"CTRL,START,{cur_mode}", "stop": "CTRL,STOP", "reset": "CTRL,RESET"}.get(a)
    if ctrl:
        try:
            sock.sendto(ctrl.encode(), PCB_CTRL_ADDR)
        except OSError:
            pass
    return {"ok": True, "msg": msg}


def link_payload(st: MonitorState) -> dict:
    with st.lock:
        return {"accepting": st.accepting, "mode": st.mode, "source": st.source, "source_ko": SRC_KO.get(st.source, st.source),
                "n_relay": st.n_relay, "n_to_pcb": st.n_to_pcb, "n_from_pcb": st.n_from_pcb, "n_received": st.n_received,
                "n_dropped": st.n_dropped, "buf_len": len(st.buf), "n_windows": st.n_windows, "status": st.status,
                "pcb_age_s": round(time.time() - st.last_pcb_wall, 2) if st.last_pcb_wall else None,
                "last_decision": st.last_decision, "pcb_is_emulator": PCB_ADDR[0] == "127.0.0.1",
                "proto": PROTO, "seq_lost": dict(st.seq_lost), "rx_log": st.rx_name,
                "n_invalid": st.n_invalid, "n_ignored": dict(st.n_ignored),
                "rtt_ms_med": round(float(np.median(st.rtt_ms)), 2) if st.rtt_ms else None,
                "rtt_ms_max": round(float(np.max(st.rtt_ms)), 2) if st.rtt_ms else None}


def buffer_payload(st: MonitorState) -> dict:
    with st.lock:
        rows = [[int(r[0]), int(r[1]), int(r[2])] + [round(float(v), 4) for v in r[3:]] for r in st.raw_buf]
        return {"cols": V2_FIELDS, "rows": rows, "window": WINDOW, "hop": HOP, "since_hop": st.since_hop,
                "n_received": st.n_received, "n_windows": st.n_windows, "source": st.source,
                "source_ko": SRC_KO.get(st.source, st.source), "accepting": st.accepting, "mode": st.mode,
                "last_decision": st.last_decision, "last_file": st.last_window_file}


def downsample(n: int) -> np.ndarray:
    return np.arange(n) if n <= MAX_PLOT_POINTS else np.linspace(0, n - 1, MAX_PLOT_POINTS).astype(int)


def gradient(x: np.ndarray) -> np.ndarray:
    g = np.zeros_like(x)
    if len(x) < 2 * MA_K:
        return g
    c = np.cumsum(np.insert(x, 0, 0.0))
    ma = (c[MA_K:] - c[:-MA_K]) / MA_K
    g[2 * MA_K - 1:] = (ma[MA_K:] - ma[:-MA_K]) / (MA_K * SAMPLE_DT)
    return g


def build_payload(st: MonitorState, thr: dict) -> dict:
    with st.lock:
        smp = list(st.samples)
        win = list(st.win)
        rate = list(st.rx_rate)
        base = {"status": st.status, "route": st.seg_state, "route_ko": ROUTE_KO.get(st.seg_state, "-"),
                "buf_len": len(st.buf), "since_hop": st.since_hop, "window": WINDOW, "hop": HOP,
                "hist_len": len(st.seg_windows), "hist_max": L_HIST, "hist_routes": list(st.hist_routes), "n_received": st.n_received,
                "n_windows": st.n_windows, "n_alert": st.n_alert, "infer_ms": round(st.last_infer_ms, 2),
                "alarm_t0": st.alarm_t0, "fault_cell": st.fault_cell, "messages": list(st.messages),
                "legacy": {"tripped": st.legacy.tripped, "timer": st.legacy.timer},
                "rx_age_s": round(time.time() - st.last_rx_wall, 2) if st.last_rx_wall else None,
                "tau": {r: {"cell": thr[r]["cell"], "pack": thr[r]["pack"]} for r in thr},
                "backend": BACKEND, "host": "Raspberry Pi 5" if ON_RASPBERRY_PI else "MacBook",
                "source": st.source, "source_ko": SRC_KO.get(st.source, st.source or "-"), "accepting": st.accepting,
                "mode": st.mode, "legacy_src": st.legacy_src, "last_file": st.last_window_file}
    base["rx_hz"] = round((rate[-1][1] - rate[0][1]) / max(rate[-1][0] - rate[0][0], 1e-6), 1) if len(rate) > 1 else 0.0
    if smp:
        t = np.array([s[0] for s in smp])
        X = np.stack([s[1] for s in smp])
        idx = downsample(len(t))
        base["t"] = t[idx].round(2).tolist()
        base["latest"] = {"t": float(t[-1]), "v": X[-1, 0:4].round(4).tolist(), "i": round(float(X[-1, 4]), 4),
                          "temp": X[-1, 5:9].round(2).tolist(), "bal": X[-1, 9:13].astype(int).tolist()}
        base["volts"] = [X[idx, k].round(4).tolist() for k in range(4)]
        base["temps"] = [X[idx, 5 + k].round(2).tolist() for k in range(4)]
        base["packI"] = X[idx, 4].round(4).tolist()
        base["grad_v"] = [(gradient(X[:, k].astype(np.float64)) * 1000)[idx].round(2).tolist() for k in range(4)]
        base["grad_t"] = [(gradient(X[:, 5 + k].astype(np.float64)) * 60)[idx].round(3).tolist() for k in range(4)]
        base["grad_i"] = gradient(X[:, 4].astype(np.float64))[idx].round(4).tolist()
    else:
        base.update({"t": [], "latest": None, "volts": [[]] * 4, "temps": [[]] * 4, "packI": [],
                     "grad_v": [[]] * 4, "grad_t": [[]] * 4, "grad_i": []})
    base["win"] = win
    return base


def make_handler(st: MonitorState, experts: dict, thr: dict):
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self) -> None:
            try:
                if self.path.startswith("/api/state"):
                    self._send(200, json.dumps(build_payload(st, thr)).encode(), "application/json")
                elif self.path.startswith("/api/buffer"):
                    self._send(200, json.dumps(buffer_payload(st)).encode(), "application/json")
                elif self.path.startswith("/api/link"):
                    self._send(200, json.dumps(link_payload(st)).encode(), "application/json")
                elif self.path.startswith("/buffer"):
                    with open(BUFFER_HTML, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                elif self.path in ("/", "/index.html"):
                    with open(HTML_PATH, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")
            except (OSError, ValueError) as e:
                self._send(500, str(e).encode(), "text/plain")

        def do_POST(self) -> None:
            try:
                body = self._json_body()
                if self.path == "/api/ingest":
                    n = ingest(st, body.get("samples", []), experts, thr, src=body.get("src", "HTTP"))
                    self._send(200, json.dumps({"ok": True, "n": n}).encode(), "application/json")
                elif self.path == "/api/relay":
                    n = relay_to_pcb(st, body.get("samples", []), out_sock)
                    self._send(200, json.dumps({"ok": True, "n": n}).encode(), "application/json")
                elif self.path == "/api/control":
                    self._send(200, json.dumps(control(st, body, out_sock)).encode(), "application/json")
                elif self.path == "/api/reset":
                    with st.lock:
                        st.reset()
                    self._send(200, b'{"ok": true}', "application/json")
                elif self.path == "/api/command":
                    cmd = body.get("cmd", "")
                    with st.lock:
                        st.messages.append(f"MCU 명령 수신: {cmd} (기록만)")
                    self._send(200, json.dumps({"ok": True, "msg": f"{cmd} 수신 (기능 미구현)"}).encode(), "application/json")
                else:
                    self._send(404, b"not found", "text/plain")
            except (ValueError, json.JSONDecodeError) as e:
                self._send(400, str(e).encode(), "text/plain")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            pass

    return Handler


def load_model(device=None) -> tuple[dict, dict]:
    try:
        with open(os.path.join(MODEL_DIR, "threshold.json"), encoding="utf-8") as f:
            thr = json.load(f)["experts"]
    except (OSError, KeyError) as e:
        raise RuntimeError(f"경계 로드 실패 ({MODEL_DIR}): {e}") from e
    return {r: Expert(os.path.join(MODEL_DIR, r), device) for r in EXPERTS}, thr


def main() -> None:
    device = torch.device("cpu") if torch is not None else None
    experts, thr = load_model(device)
    st = MonitorState()
    threading.Thread(target=udp_loop, args=(st, experts, thr), daemon=True).start()
    threading.Thread(target=relay_loop, args=(st,), daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), make_handler(st, experts, thr))
    print(f"[AE monitor] 모델 {MODEL_NAME} 로드 ({MODEL_DIR}, backend {BACKEND}) — http://0.0.0.0:{PORT}  "
          f"(입력 UDP {UDP_IN_PORT}, 규약 {PROTO})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("종료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[오류] 모니터 서버 실패: {e}")
        raise
