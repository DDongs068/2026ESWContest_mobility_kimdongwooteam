import csv
import io
import json
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_HOST: str = "10.55.0.2"
HTTP_PORT: int = 8061
FRAME_PORT: int = 5020
RECEIVER: tuple[str, int] = ("127.0.0.1", 5013)
MONITOR: str = "http://127.0.0.1:8050"
RELAY_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "relay")
DATA_DIR: str = os.path.join(RELAY_DIR, "data")
LABEL_FILE: str = os.path.join(RELAY_DIR, "label.txt")
TAIL_BYTES: int = 16384
SNAP_ROWS: int = 10
LIVE_MAX_AGE_S: float = 3.0
SIM_EMERGENCY: int = 2
SIM_QUEUE_MAX: int = 50
MCU_SYNC_WAIT_S: float = 2.0
V2_FIELDS: list[str] = ["seq", "ms", "valid", "V1", "V2", "V3", "V4", "T1", "T2", "T3", "T4", "I", "STACK", "PACK", "LD"]


class State:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: bool = False
        self.sim_seq: int = 0
        self.sent: int = 0
        self.sim_ok: int = 0
        self.sim_rejected: int = 0
        self.sim_lost: int = 0
        self.last_sim_rc: int | None = None


ST = State()
MCU_QUEUE: queue.Queue = queue.Queue(maxsize=SIM_QUEUE_MAX)


def monitor_call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MONITOR + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def write_label(text: str) -> None:
    try:
        with open(LABEL_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def tail_rows(path: str) -> list[list[str]]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return list(csv.reader(io.StringIO(text.split("\n", 1)[-1])))


def parse_v2(row: list[str]) -> tuple[float, dict] | None:
    try:
        if len(row) < 16 or row[3] != "1":
            return None
        v = dict(zip(V2_FIELDS, row[1:16]))
        return datetime.fromisoformat(row[0]).timestamp(), {k: float(x) for k, x in v.items()}
    except ValueError:
        return None


def live_snapshot() -> dict:
    day = time.strftime("%Y%m%d")
    rows = [p for p in map(parse_v2, tail_rows(f"{DATA_DIR}/bms_{day}.csv")) if p]
    if not rows:
        return {"fresh": False, "age_s": None}
    rows.sort(key=lambda p: p[0])
    age = time.time() - rows[-1][0]
    last = [v for _, v in rows[-SNAP_ROWS:]]
    mean = lambda k: round(sum(r[k] for r in last) / len(last), 4)
    return {"fresh": age <= LIVE_MAX_AGE_S, "age_s": round(age, 1), "n": len(last),
            "V": [mean(f"V{k}") for k in range(1, 5)], "T": [mean(f"T{k}") for k in range(1, 5)],
            "I": mean("I"), "STACK": mean("STACK"), "PACK": mean("PACK"), "LD": mean("LD")}


def mcu_send(cmd: str) -> dict:
    return monitor_call("/api/mcu", {"cmd": cmd}).get("reply") or {}


def mcu_worker() -> None:
    while True:
        cmd, done = MCU_QUEUE.get()
        reply = mcu_send(cmd)
        if done is not None:
            done.put(reply)
            continue
        rc = reply.get("rc")
        with ST.lock:
            if rc is None:
                ST.sim_lost += 1
            elif rc < 0:
                ST.sim_rejected += 1
                ST.last_sim_rc = rc
            else:
                ST.sim_ok += 1
                ST.last_sim_rc = rc


def mcu_sync(cmd: str) -> dict:
    done: queue.Queue = queue.Queue(maxsize=1)
    try:
        MCU_QUEUE.put((cmd, done), timeout=MCU_SYNC_WAIT_S)
        return done.get(timeout=MCU_SYNC_WAIT_S)
    except (queue.Full, queue.Empty):
        return {}


def drain_sim_queue() -> None:
    try:
        while True:
            MCU_QUEUE.get_nowait()
    except queue.Empty:
        pass


def sim_command(seq: int, line: str) -> str | None:
    valid, values = line.split(",", 1)
    return f"SIM {seq},{seq * 100},{SIM_EMERGENCY},{values}" if float(valid) == 1 else None


def forward(sock: socket.socket, index: int, line: str) -> None:
    with ST.lock:
        if not ST.active:
            return
        seq = ST.sim_seq
        ST.sim_seq += 1
        ST.sent += 1
    cmd = sim_command(seq, line)
    if cmd is not None:
        try:
            MCU_QUEUE.put_nowait((cmd, None))
        except queue.Full:
            with ST.lock:
                ST.sim_lost += 1
    sock.sendto(f"{index + 1},{index * 100},{line}\n".encode(), RECEIVER)


def frame_loop() -> None:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind((BIND_HOST, FRAME_PORT))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        try:
            data = rx.recv(2048).decode()
            index, line = data.strip().split(",", 1)
            forward(tx, int(index), line)
        except (OSError, ValueError) as e:
            print(f"[경고] 프레임 처리 실패: {e}", flush=True)


def session_start(body: dict) -> dict:
    panel = "charge" if body.get("charge") else "motor"
    monitor_call("/api/control", {"action": "reset", "mode": "abnormal"})
    monitor_call("/api/control", {"action": "start", "mode": "abnormal", "panel": panel})
    write_label("비정상")
    drain_sim_queue()
    status = mcu_sync("STATUS")
    if status and not status.get("started"):
        mcu_sync("START")
    cleared = mcu_sync("SIM CLEAR")
    with ST.lock:
        ST.active, ST.sent, ST.sim_seq = True, 0, 0
        ST.sim_ok, ST.sim_rejected, ST.sim_lost, ST.last_sim_rc = 0, 0, 0, None
    return {"ok": True, "protocol": "sim" if status and cleared else "unknown"}


def session_stop() -> dict:
    with ST.lock:
        ST.active = False
        sent = ST.sent
    drain_sim_queue()
    mcu_sync("SIM CLEAR")
    monitor_call("/api/control", {"action": "reset", "mode": "normal"})
    monitor_call("/api/control", {"action": "start", "mode": "normal", "panel": "motor"})
    write_label("정상")
    return {"ok": True, "sent": sent}


def full_state() -> dict:
    with ST.lock:
        active, sent = ST.active, ST.sent
        sim = {"ok": ST.sim_ok, "rejected": ST.sim_rejected, "lost": ST.sim_lost, "last_rc": ST.last_sim_rc}
    m = monitor_call("/api/state")
    p = m.get("pcb") or {}
    online = bool(p.get("online")) and not p.get("emu")
    return {"live": live_snapshot(), "active": active, "sent": sent,
            "pcb": {"online": online, "protocol": "sim" if online else "unknown", "fault_bits": p.get("sim_bits") or 0,
                    "real_fault": p.get("fault"), "stage": p.get("stage"), "m1": p.get("m1"), "sim": sim},
            "monitor": {"online": bool(m), "status": m.get("status"), "fault_cell": m.get("fault_cell"),
                        "buf_len": m.get("buf_len"), "mode": m.get("mode")}}


class Handler(BaseHTTPRequestHandler):

    def reply(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.reply(full_state() if self.path == "/state" else {"error": "없는 경로"}, 200 if self.path == "/state" else 404)

    def do_POST(self) -> None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/session/start":
                self.reply(session_start(body))
            elif self.path == "/session/stop":
                self.reply(session_stop())
            else:
                self.reply({"error": "없는 경로"}, 404)
        except (ValueError, OSError) as e:
            self.reply({"error": str(e)}, 500)

    def log_message(self, *_: object) -> None:
        pass


def main() -> None:
    threading.Thread(target=mcu_worker, daemon=True).start()
    threading.Thread(target=frame_loop, daemon=True).start()
    print(f"컨트롤 패널 에이전트: http://{BIND_HOST}:{HTTP_PORT}", flush=True)
    ThreadingHTTPServer((BIND_HOST, HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"[오류] 에이전트 시작 실패 ({BIND_HOST}:{HTTP_PORT}): {e}")
        raise
