import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from frames import T_COLS, V_COLS, fit_to_measured, load_sim_csv, splice_fault, to_line

ROOT: str = os.path.dirname(os.path.abspath(__file__))
HTML_FILE: str = f"{ROOT}/panel.html"
RUN_DIR: str = f"{ROOT}/runtime"
JOB_DIR: str = f"{RUN_DIR}/jobs"
SAVE_DIR: str = f"{RUN_DIR}/runs"
WORKER_LOG: str = f"{RUN_DIR}/worker.log"
READY_FILE: str = f"{RUN_DIR}/worker.ready"
MATLAB_BIN: str = "/Applications/MATLAB_R2025b.app/bin/matlab"
WORKER_M: str = f"{ROOT}/sim_worker.m"
HOST: str = "127.0.0.1"
PORT: int = 8060
PI_HOST: str = os.environ.get("PANEL_PI_HOST", "10.55.0.2")
PI_HTTP: str = f"http://{PI_HOST}:8061"
PI_FRAME: tuple[str, int] = (PI_HOST, 5020)
PRE_S: float = 10.0
DUR_S: float = 20.0
FAULT_AT_S: float = 5.0
ISC_R_OHM: float = 0.05
ISC_RAMP_S: float = 20.0
OC_CV_V: float = 20.0
OC_TARGET_V: float = 4.256
OC_CHECK_S: float = 16.0
OC_V_PER_A: float = 0.068
OC_CC_MAX_A: float = 10.0
CHARGE_AE_WARN_V: float = 4.08
HZ: float = 10.0
JOB_TIMEOUT_S: float = 120.0
LOG_KEEP: int = 40
I_MOTOR_A: float = -0.2
I_REST_A: float = 0.2
I_CHARGE_A: float = 0.03
SCENARIOS: dict[str, dict] = {
    "isc_drive": {"name": "Soft ISC 주행", "charge": False},
    "isc_rest": {"name": "Soft ISC 휴지", "charge": False},
    "isc_charge": {"name": "Soft ISC 충전", "charge": True},
    "overcharge": {"name": "과충전", "charge": True},
}


class Panel:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.session: dict = {"phase": "idle"}
        self.log: list[dict] = []
        self.pi: dict = {}
        self.pi_tick: float = 0.0
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def say(self, text: str, level: str = "info") -> None:
        with self.lock:
            self.log.append({"t": time.strftime("%H:%M:%S"), "text": text, "level": level})
            del self.log[:-LOG_KEEP]
        print(text, flush=True)

    def update(self, **kw: object) -> None:
        with self.lock:
            self.session.update(kw)


PANEL = Panel()


def pi_call(path: str, body: dict | None = None, timeout: float = 2.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PI_HTTP + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"라즈베리파이 에이전트에 연결하지 못함 ({PI_HTTP}{path}): {e}") from e


def pi_poll_loop() -> None:
    while True:
        try:
            s = pi_call("/state")
            with PANEL.lock:
                PANEL.pi, PANEL.pi_tick = s, time.time()
        except RuntimeError:
            pass
        time.sleep(0.5)


def worker_state() -> str:
    if subprocess.run(["pgrep", "-f", "sim_worker.m"], capture_output=True).returncode != 0:
        return "off"
    return "ready" if os.path.exists(READY_FILE) else "starting"


def ensure_worker() -> None:
    if worker_state() != "off":
        return
    if os.path.exists(READY_FILE):
        os.remove(READY_FILE)
    os.makedirs(JOB_DIR, exist_ok=True)
    with open(WORKER_LOG, "ab") as log:
        subprocess.Popen([MATLAB_BIN, "-batch", f"run('{WORKER_M}')"], stdout=log, stderr=log, start_new_session=True)
    PANEL.say("MATLAB 을 준비하는 중입니다. 약 1 분 걸립니다.")


def precheck(scenario: str, live: dict) -> str:
    if not live.get("fresh"):
        return "회로 값이 들어오지 않습니다. PCB 전원과 LAN 을 확인하고, 재생 뒤라면 PCB 를 리셋해 주세요."
    i = float(live["I"])
    if scenario == "isc_drive" and i >= I_MOTOR_A:
        return f"모터가 돌고 있지 않습니다 (전류 {i:+.2f} A). 모터를 켠 뒤 다시 눌러 주세요."
    if scenario == "isc_rest" and abs(i) >= I_REST_A:
        return f"휴지 상태가 아닙니다 (전류 {i:+.2f} A). 모터를 끄고 충전 단자를 뺀 뒤 다시 눌러 주세요."
    if SCENARIOS[scenario]["charge"] and i <= I_CHARGE_A:
        return f"충전 중이 아닙니다 (전류 {i:+.2f} A). 충전 단자를 연결한 뒤 다시 눌러 주세요."
    return ""


def run_job(jid: str, job: dict) -> tuple[pd.DataFrame, dict]:
    p = os.path.join(JOB_DIR, jid)
    with open(p + ".json.part", "w", encoding="utf-8") as f:
        json.dump(job, f)
    os.replace(p + ".json.part", p + ".json")
    t0 = time.time()
    while not os.path.exists(p + ".done"):
        if PANEL.stop_evt.is_set():
            raise InterruptedError
        if time.time() - t0 > JOB_TIMEOUT_S:
            raise RuntimeError(f"MATLAB 이 {JOB_TIMEOUT_S:.0f} 초 안에 끝나지 않았습니다. {WORKER_LOG} 를 확인해 주세요.")
        time.sleep(0.1)
    with open(p + ".done", encoding="utf-8") as f:
        done = json.load(f)
    if not done.get("ok"):
        raise RuntimeError(f"시뮬레이션 실패: {done.get('error')}")
    return load_sim_csv(p + ".csv"), done


def cleanup_job(jid: str) -> None:
    for ext in (".json", ".csv", ".done"):
        try:
            os.remove(os.path.join(JOB_DIR, jid + ext))
        except OSError:
            pass


def save_run(jid: str, cfg: dict, snap: dict, frames: pd.DataFrame, sent: int) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    base = os.path.join(SAVE_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{cfg['scenario']}")
    frames.iloc[:sent].to_csv(base + ".csv.part", index=False)
    os.replace(base + ".csv.part", base + ".csv")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"id": jid, "config": cfg, "snapshot": snap, "sent": sent}, f, ensure_ascii=False, indent=1)
    return base + ".csv"


def send_frames(frames: pd.DataFrame, start_v: list[float]) -> int:
    lines = [to_line(r) for _, r in frames.iterrows()]
    t0, n = time.monotonic(), 0
    for k, line in enumerate(lines):
        if PANEL.stop_evt.is_set():
            break
        PANEL.tx.sendto(f"{k},{line}".encode(), PI_FRAME)
        n = k + 1
        if k % 2 == 0:
            r = frames.iloc[k]
            PANEL.update(sent=n, frame={"V": r[V_COLS].tolist(), "T": r[T_COLS].tolist(), "I": float(r["I"]),
                                        "STACK": float(r["STACK"]),
                                        "dV_mV": [round((float(r[c]) - s) * 1000, 1) for c, s in zip(V_COLS, start_v)]})
        wait = t0 + (k + 1) / HZ - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    PANEL.update(sent=n)
    return n


def fault_cc(snap: dict) -> float:
    i0 = float(snap["I"])
    return float(min(max(i0 + (OC_TARGET_V - 0.04 - max(snap["V"])) / OC_V_PER_A, i0 + 0.5), OC_CC_MAX_A))


def overcharge_peak(sim: pd.DataFrame, snap: dict) -> float:
    fitted, _ = fit_to_measured(sim, snap, seed=0, noise=False)
    return float(fitted[fitted["ms"] <= OC_CHECK_S * 1000][V_COLS].iloc[-1].max())


def build_sim(cfg: dict, snap: dict, jid: str) -> tuple[pd.DataFrame, dict]:
    base = {"scenario": cfg["scenario"], "cell": int(cfg.get("cell", 2)), "V": snap["V"], "T": snap["T"], "I": snap["I"],
            "bms_off": True, "pre_s": PRE_S}
    if cfg["scenario"] != "overcharge":
        return run_job(jid, {**base, "id": jid, "R_ohm": ISC_R_OHM, "ramp_s": ISC_RAMP_S, "isc_delay": FAULT_AT_S,
                             "cv_V": 17.2, "dur_s": DUR_S})
    nid = jid + "n"
    normal, _ = run_job(nid, {**base, "id": nid, "R_ohm": 3.0, "cv_V": 16.8, "dur_s": DUR_S})
    cleanup_job(nid)
    job = {**base, "R_ohm": 3.0, "cv_V": OC_CV_V, "pre_s": 0.5, "dur_s": DUR_S - FAULT_AT_S}
    fid, cc = jid + "f", fault_cc(snap)
    fault, _ = run_job(fid, {**job, "id": fid, "cc_A": cc})
    cleanup_job(fid)
    cc = min(cc + (OC_TARGET_V - overcharge_peak(splice_fault(normal, fault, FAULT_AT_S), snap)) / OC_V_PER_A, OC_CC_MAX_A)
    fault, done = run_job(jid, {**job, "id": jid, "cc_A": cc})
    sim = splice_fault(normal, fault, FAULT_AT_S)
    peak = overcharge_peak(sim, snap)
    PANEL.say(f"충전기 고장 조건: 전압 제어 상실, 전류 {cc:.1f} A, {OC_CHECK_S:.0f} 초에 최고 셀 {peak:.3f} V")
    if peak <= 4.25:
        PANEL.say(f"시작 전압이 낮아 전류 상한 {OC_CC_MAX_A:.0f} A 로도 4.25 V 에 못 미칩니다. MCU 차단이 안 날 수 있습니다.", "warn")
    return sim, done


def session_thread(cfg: dict, snap: dict) -> None:
    name, jid = SCENARIOS[cfg["scenario"]]["name"], f"j{int(time.time() * 1000)}"
    sent, frames, started = 0, None, False
    try:
        PANEL.say(f"{name} 시작. 실측 전압 {snap['V']} V, 전류 {snap['I']:+.3f} A")
        PANEL.update(phase="init", started=time.time())
        if SCENARIOS[cfg["scenario"]]["charge"] and max(snap["V"]) >= CHARGE_AE_WARN_V:
            PANEL.say(f"최고 셀이 {max(snap['V']):.2f} V 입니다. 이 전압의 충전에서는 고장 없이도 AE 경보가 납니다.", "warn")
        PANEL.update(phase="sim")
        sim, done = build_sim(cfg, snap, jid)
        PANEL.say(f"MATLAB 재현 완료 ({done['wall_s']:.1f} 초). 초기 SOC {[round(s * 100, 1) for s in done['soc0']]} %")
        frames, corr = fit_to_measured(sim, snap, seed=int(time.time()))
        PANEL.update(soc0=done["soc0"], correction=corr, total=len(frames), wall_s=done["wall_s"])
        PANEL.say(f"실측에 맞춘 보정: 전압 {corr['V_mV']} mV, 전류 {corr['I_A']:+.3f} A")
        st = pi_call("/session/start", {"scenario": cfg["scenario"], "charge": SCENARIOS[cfg["scenario"]]["charge"]})
        started = True
        if st.get("protocol") == "unknown":
            PANEL.say("MCU 가 응답하지 않습니다. 모니터로만 전송합니다.", "warn")
        PANEL.update(phase="send", sent=0)
        start_v = frames[V_COLS].iloc[:10].mean().tolist()
        sent = send_frames(frames, start_v)
        if not PANEL.stop_evt.is_set():
            PANEL.update(phase="done")
            PANEL.say(f"{name}: 준비한 데이터를 모두 보냈습니다 ({sent / HZ:.0f} 초). 중지를 누르면 끝납니다.")
            PANEL.stop_evt.wait()
    except InterruptedError:
        pass
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        PANEL.say(str(e), "error")
        PANEL.update(error=str(e))
    finally:
        finish_session(jid, cfg, snap, frames, sent, started, name)


def finish_session(jid: str, cfg: dict, snap: dict, frames: pd.DataFrame | None, sent: int, started: bool, name: str) -> None:
    if started:
        try:
            pi_call("/session/stop", {})
        except RuntimeError as e:
            PANEL.say(str(e), "error")
    if frames is not None and sent:
        try:
            PANEL.say(f"{name} 종료. {sent} 프레임을 보냈습니다. 기록: {save_run(jid, cfg, snap, frames, sent)}")
        except OSError as e:
            PANEL.say(f"기록 저장 실패: {e}", "error")
    else:
        PANEL.say(f"{name} 종료. 보낸 데이터는 없습니다.")
    threading.Thread(target=cleanup_when_done, args=(jid,), daemon=True).start()
    with PANEL.lock:
        err = PANEL.session.get("error")
        PANEL.session = {"phase": "idle", "last_error": err}


def cleanup_when_done(jid: str) -> None:
    t0 = time.time()
    while os.path.exists(os.path.join(JOB_DIR, jid + ".json")) and time.time() - t0 < JOB_TIMEOUT_S:
        time.sleep(0.5)
    while not os.path.exists(os.path.join(JOB_DIR, jid + ".done")) and time.time() - t0 < JOB_TIMEOUT_S:
        time.sleep(0.5)
    cleanup_job(jid)


def api_start(body: dict) -> tuple[dict, int]:
    sc = body.get("scenario")
    if sc not in SCENARIOS:
        return {"error": f"알 수 없는 시나리오: {sc}"}, 400
    with PANEL.lock:
        busy = PANEL.session.get("phase") != "idle"
    if busy:
        return {"error": "다른 시나리오가 진행 중입니다. 먼저 중지해 주세요."}, 409
    if worker_state() != "ready":
        ensure_worker()
        return {"error": "MATLAB 을 준비하는 중입니다. 잠시 뒤 다시 눌러 주세요."}, 409
    try:
        live = pi_call("/state")["live"]
    except (RuntimeError, KeyError) as e:
        return {"error": str(e)}, 502
    msg = precheck(sc, live)
    if msg:
        return {"error": msg}, 409
    PANEL.stop_evt.clear()
    PANEL.update(phase="read", scenario=sc, cfg=body, snapshot=live, started=time.time(), error=None, last_error=None,
                 sent=0, total=None, frame=None, correction=None, soc0=None)
    threading.Thread(target=session_thread, args=(dict(body, scenario=sc), live), daemon=True).start()
    return {"ok": True}, 200


def api_state() -> dict:
    with PANEL.lock:
        s, pi, age, log = dict(PANEL.session), dict(PANEL.pi), time.time() - PANEL.pi_tick, list(PANEL.log)
    return {"session": s, "pi": pi, "pi_online": age < 3.0, "matlab": worker_state(), "log": log, "now": time.time()}


class Handler(BaseHTTPRequestHandler):

    def reply(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self.reply(api_state())
        elif self.path == "/":
            try:
                with open(HTML_FILE, "rb") as f:
                    data = f.read()
            except OSError as e:
                self.reply({"error": f"{HTML_FILE}: {e}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.reply({"error": "없는 경로"}, 404)

    def do_POST(self) -> None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            self.reply({"error": "요청 형식 오류"}, 400)
            return
        if self.path == "/api/start":
            obj, code = api_start(body)
            self.reply(obj, code)
        elif self.path == "/api/stop":
            PANEL.stop_evt.set()
            self.reply({"ok": True})
        else:
            self.reply({"error": "없는 경로"}, 404)

    def log_message(self, *_: object) -> None:
        pass


def main() -> None:
    os.makedirs(JOB_DIR, exist_ok=True)
    ensure_worker()
    threading.Thread(target=pi_poll_loop, daemon=True).start()
    print(f"컨트롤 패널: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"[오류] 패널 서버 시작 실패 ({HOST}:{PORT}): {e}")
        raise
