#!/bin/bash
AIBMS_ROOT="$(cd "$(dirname "$0")" && pwd)" exec python3 - "$@" <<'PY'
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PI: str = "rpi4@10.55.0.2"
PI_IP: str = "10.55.0.2"
MCU_IP: str = "192.168.50.10"
MCU_MAC: str = "02:42:4d:53:00:01"
ETH_IP: str = "192.168.50.2"
MONITOR: str = f"http://{PI_IP}:8050"
AGENT: str = f"http://{PI_IP}:8061"
PANEL: str = "http://127.0.0.1:8060"
PANEL_DIR: str = os.path.join(os.environ["AIBMS_ROOT"], "ControlPanel")
JOB_DIR: str = f"{PANEL_DIR}/runtime/jobs"
READY_FILE: str = f"{PANEL_DIR}/runtime/worker.ready"
DATA_DIR: str = "/home/rpi4/2026ESWContest_mobility_kimdongwooteam/AI_AutoEncoder(RaspberryPi)/RaspberryPi/relay/data"
ACTIVE_SYSTEM: list[str] = ["pcb-receiver"]
ACTIVE_USER: list[str] = ["pcb-calc-buffer", "pcb-calc-buffer-abnormal", "pcb-excel-to-w10",
                          "pcb-excel-to-w10-abnormal", "pcb-receiver-abnormal"]
RETIRED_SYSTEM: list[str] = ["pcb-relay"]
RETIRED_USER: list[str] = ["pcb-ack-gate"]
V_RANGE: tuple[float, float] = (2.5, 4.3)
T_RANGE: tuple[float, float] = (0.0, 60.0)
I_REST_A: float = 0.2
I_MOTOR_A: float = -0.1
I_CHARGE_A: float = 0.03
RATE_MIN_HZ: float = 8.0
SENSE_S: float = 6.0
AE_MAX_S: float = 12.0
MCU_MAX_S: float = 19.0
HTTP_TIMEOUT_S: float = 6.0
SSH_TIMEOUT_S: float = 15.0

READONLY: bool = "--readonly" in sys.argv
results: list[tuple[str, str, str]] = []


def report(state: str, name: str, info: object = "") -> bool:
    results.append((state, name, str(info)))
    color = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}[state] if sys.stdout.isatty() else ""
    reset = "\033[0m" if color else ""
    print(f"  {color}{state}{reset}  {name}" + (f"  ({info})" if info != "" else ""), flush=True)
    return state == "PASS"


def check(name: str, ok: bool, info: object = "") -> bool:
    return report("PASS" if ok else "FAIL", name, info)


def skip(name: str, why: str) -> None:
    report("SKIP", name, why)


def section(title: str) -> None:
    print(f"\n[{title}]", flush=True)


def ssh(cmd: str, stdin: str | None = None, timeout: float = SSH_TIMEOUT_S) -> tuple[int, str]:
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", PI, f"export LC_ALL=C.UTF-8; {cmd}"]
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout,
                           stdin=None if stdin is not None else subprocess.DEVNULL)
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 255, str(e)


def http(url: str, body: dict | None = None, timeout: float = HTTP_TIMEOUT_S) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.loads(e.read() or b"{}")}
    except (urllib.error.URLError, ValueError) as e:
        raise OSError(str(e)) from e


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def mcu(cmd: str) -> dict:
    body = json.dumps({"cmd": cmd})
    rc, out = ssh(f"curl -s -m 3 -X POST -H 'Content-Type: application/json' -d '{body}' http://127.0.0.1:8050/api/mcu")
    try:
        return json.loads(out).get("reply") or {}
    except ValueError:
        return {}


def sim_rows(rows: list[str]) -> dict:
    script = (f"import json,urllib.request as u\nlast={{}}\nfor c in json.loads({json.dumps(rows)!r}):\n"
              " q=u.Request('http://127.0.0.1:8050/api/mcu',json.dumps({'cmd':c}).encode(),{'Content-Type':'application/json'})\n"
              " last=json.load(u.urlopen(q,timeout=3)).get('reply') or {}\nprint(json.dumps(last))\n")
    rc, out = ssh("python3 -", stdin=script, timeout=30)
    try:
        return json.loads(out)
    except ValueError:
        return {}


def sim_row(seq: int, v1: float = 3.7, v2: float = 3.7, t1: float = 25.0, i: float = -1.0) -> str:
    return f"SIM {seq},{seq * 100},2,{v1},{v2},3.7,3.7,{t1},25,25,25,{i},14.8,14.8,14.8"


def monitor() -> dict:
    try:
        return http(MONITOR + "/api/state")
    except OSError:
        return {}


def panel() -> dict:
    try:
        return http(PANEL + "/api/state")
    except OSError:
        return {}


def button(action: str) -> dict:
    return http(MONITOR + "/api/motor", {"action": action}, timeout=15)


def ask(question: str) -> bool:
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(f"\n  {question} [y/N] ")
            tty.flush()
            return tty.readline().strip().lower() == "y"
    except OSError:
        return False


def csv_stats(start: int) -> dict:
    script = f"""
import csv,json,time
try:
    r=list(csv.DictReader(open('{DATA_DIR}/bms_'+time.strftime('%Y%m%d')+'.csv',newline='')))
except OSError:
    r=[]
n=r[{start}:]
ok=[x for x in n if x['valid']=='1']
v=[float(x[k]) for x in ok for k in ('V1','V2','V3','V4')]
t=[float(x[k]) for x in ok for k in ('T1','T2','T3','T4')]
i=[float(x['I']) for x in ok]
print(json.dumps(dict(total=len(r),new=len(n),invalid=len(n)-len(ok),lost=sum(int(x['lost'] or 0) for x in n),
  vmin=min(v) if v else None,vmax=max(v) if v else None,tmin=min(t) if t else None,tmax=max(t) if t else None,
  imean=sum(i)/len(i) if i else None)))
"""
    rc, out = ssh("python3 -", stdin=script)
    try:
        return json.loads(out)
    except ValueError:
        return {}


def test_link() -> bool:
    section("1. 통신")
    r = subprocess.run(["ping", "-c", "1", "-t", "2", PI_IP], capture_output=True)
    usb = check("Mac → Pi USB ping", r.returncode == 0, PI_IP)
    rc, out = ssh("hostname")
    pi_ok = check("Mac → Pi SSH", rc == 0, out[:40])
    if not (usb and pi_ok):
        skip("Pi 이후 통신", "Pi 연결 없음")
        return False
    rc, out = ssh("cat /sys/class/net/eth0/carrier 2>/dev/null; ip -4 -br addr show eth0")
    lines = out.splitlines()
    check("Pi eth0 링크", bool(lines) and lines[0] == "1", "보드 전원·랜선 확인" if not lines or lines[0] != "1" else "")
    check(f"Pi eth0 주소 {ETH_IP}", ETH_IP in out, lines[-1][:50] if lines else "")
    rc, _ = ssh(f"ping -c 2 -W 1 {MCU_IP}")
    check("Pi → MCU ping", rc == 0, MCU_IP)
    rc, out = ssh(f"ip neigh show dev eth0 | grep {MCU_IP}")
    check("MCU MAC", MCU_MAC in out, out[:60])
    return True


def test_services() -> None:
    section("2. Pi 서비스")
    for s in ACTIVE_SYSTEM:
        rc, out = ssh(f"systemctl is-active {s}")
        check(f"{s} 실행 중", out == "active", out)
    for s in ACTIVE_USER:
        rc, out = ssh(f"systemctl --user is-active {s}")
        check(f"{s} 실행 중", out == "active", out)
    for s in RETIRED_SYSTEM:
        rc, out = ssh(f"systemctl is-active {s}")
        check(f"{s} 꺼짐 (옛 방식)", out != "active", out)
    for s in RETIRED_USER:
        rc, out = ssh(f"systemctl --user is-active {s}")
        check(f"{s} 꺼짐 (옛 방식)", out != "active", out)
    rc, out = ssh("systemctl show pcb-receiver -p Environment --value")
    check("receiver 허용 대역 192.168.50.0/24", "192.168.50.0/24" in out, out[:60])


def test_ui() -> tuple[bool, bool]:
    section("3. UI")
    up = check("UI 1 모니터 화면", http_ok(MONITOR + "/"), MONITOR)
    s = monitor()
    check("UI 1 상태 API", bool(s), s.get("status", ""))
    mcu_up = bool(s) and bool(s["pcb"].get("online")) and not s["pcb"].get("emu")
    check("UI 1 → MCU 연결", mcu_up, "" if mcu_up else "모니터가 MCU 응답을 받지 못함")
    try:
        with urllib.request.urlopen(MONITOR + "/", timeout=HTTP_TIMEOUT_S) as r:
            page = r.read().decode("utf-8", "ignore")
        check("UI 1 버튼 코드", all(b in page for b in ("btnDrive", "btnOn", "btnOff", "btnChg")) and "drive_active" in page)
    except (urllib.error.URLError, OSError) as e:
        check("UI 1 버튼 코드", False, e)
    try:
        with socket.create_connection(("127.0.0.1", 8050), timeout=1):
            check("Mac 에 모니터 없음", False, "localhost:8050 이 켜져 있음. 실제 모터가 돌 수 있으니 끄세요")
    except OSError:
        check("Mac 에 모니터 없음", True)
    check("UI 2 패널 화면", http_ok(PANEL + "/"), PANEL)
    p = panel()
    if p:
        check("UI 2 → Pi", bool(p.get("pi_online")))
        check("UI 2 → AE 모니터", bool(p.get("pi", {}).get("monitor", {}).get("online")))
        check("UI 2 → MCU", bool(p.get("pi", {}).get("pcb", {}).get("online")))
    else:
        skip("UI 2 연결", "패널 응답 없음. ControlPanel/run.sh 를 실행하세요")
    try:
        a = http(AGENT + "/state")
        check("pi_agent 응답", "pcb" in a, AGENT)
    except OSError as e:
        check("pi_agent 응답", False, e)
    return up, mcu_up


def test_firmware() -> bool:
    section("4. MCU 펌웨어")
    st = mcu("STATUS")
    if not check("STATUS 응답", bool(st)):
        return False
    check("판정 펌웨어 (algo=1, sim_bits 있음)", st.get("algo") == 1 and "sim_bits" in st, f"algo={st.get('algo')}")
    check("옛 펌웨어 아님", "collect_state" not in st and "csv_fault" not in st)
    check("ESTOP 정상", st.get("estop") == 1)
    check("모터 드라이버 오류 없음", st.get("fault") == 0, st.get("fault"))
    check("설정·읽기 오류 없음", st.get("sample_rc") == 0 and st.get("fet_off_rc") == 0, f"sample_rc={st.get('sample_rc')}, fet_off_rc={st.get('fet_off_rc')}")
    check("폴트 없음", not st.get("sim_fault"), f"sim_bits={st.get('sim_bits')}")
    return True


def test_matlab(idle: bool) -> None:
    section("5. MATLAB")
    ready = os.path.exists(READY_FILE)
    if not check("워커 준비됨", ready, "" if ready else "ControlPanel/run.sh 후 약 1 분"):
        return
    if READONLY:
        skip("시뮬레이션 실행", "--readonly")
        return
    if not idle:
        skip("시뮬레이션 실행", "시나리오 진행 중")
        return
    jid = f"test{int(time.time() * 1000)}"
    path = f"{JOB_DIR}/{jid}"
    job = {"id": jid, "scenario": "isc_rest", "cell": 2, "R_ohm": 0.05, "cv_V": 17.2, "ramp_s": 5.0, "isc_delay": 1.0,
           "bms_off": True, "pre_s": 1.0, "dur_s": 6.0, "V": [3.9, 3.9, 3.9, 3.9], "T": [25, 25, 25, 25], "I": 0.0}
    try:
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(job, f)
        os.replace(path + ".part", path + ".json")
        t0 = time.time()
        while not os.path.exists(path + ".done") and time.time() - t0 < 60:
            time.sleep(0.2)
        if not check("작업 완료", os.path.exists(path + ".done"), f"{time.time() - t0:.1f} s"):
            return
        with open(path + ".done", encoding="utf-8") as f:
            done = json.load(f)
        if not check("시뮬레이션 성공", bool(done.get("ok")), done.get("error", "")):
            return
        with open(path + ".csv", encoding="utf-8") as f:
            rows = [ln.strip().split(",") for ln in f][1:]
        check("출력 10 Hz", len(rows) == 60, f"{len(rows)} 행")
        v1, v2 = float(rows[-1][1]), float(rows[-1][2])
        check("물리 반응: 누설 셀 전압 하락", v2 < v1 - 0.05, f"셀1 {v1:.3f} V, 셀2 {v2:.3f} V")
    except (OSError, ValueError, IndexError) as e:
        check("시뮬레이션", False, e)
    finally:
        for ext in (".json", ".csv", ".done", ".part"):
            try:
                os.remove(path + ext)
            except OSError:
                pass


def test_sensing() -> None:
    section("6. 센싱")
    http(MONITOR + "/api/control", {"action": "reset", "mode": "normal"})
    http(MONITOR + "/api/control", {"action": "start", "mode": "normal"})
    before = csv_stats(0).get("total", 0)
    r = mcu("START")
    check("START", r.get("rc") == 0, f"rc={r.get('rc')}")
    time.sleep(SENSE_S + 2.0)
    st = mcu("STATUS")
    check("MCU 실측 유효", st.get("valid") == 1, f"sample_rc={st.get('sample_rc')}")
    c = csv_stats(before)
    new = c.get("new", 0)
    check("Pi 수신", new > 0, f"{new} 행")
    if not new:
        return
    rate = new / (SENSE_S + 2.0)
    check("수신 속도", rate >= RATE_MIN_HZ, f"{rate:.1f} Hz")
    check("무효·유실 없음", c.get("invalid") == 0 and c.get("lost") == 0, f"무효 {c.get('invalid')}, 유실 {c.get('lost')}")
    if c.get("vmin") is not None:
        check("셀 전압 범위", V_RANGE[0] < c["vmin"] and c["vmax"] < V_RANGE[1], f"{c['vmin']:.3f}~{c['vmax']:.3f} V")
        check("온도 범위", T_RANGE[0] < c["tmin"] and c["tmax"] < T_RANGE[1], f"{c['tmin']:.1f}~{c['tmax']:.1f} °C")
        check("휴지 전류", abs(c["imean"]) < I_REST_A, f"{c['imean']:+.3f} A")
    s = monitor()
    check("모니터까지 도달", s.get("n_received", 0) > 0 and s.get("source") == "PCB", f"{s.get('n_received')} 표본, 출처 {s.get('source')}")
    check("AE 판정 동작", s.get("n_windows", 0) > 0, f"{s.get('n_windows')} 회, {s.get('status')}")
    check("휴지 상태 AE 정상", s.get("status") == "NORMAL", s.get("status"))


def test_judge() -> None:
    section("7. MCU 판정 (출력 꺼짐, FAULT LED 만 켜짐)")
    try:
        mcu("SIM CLEAR")
        r = sim_rows([sim_row(k, v1=4.3) for k in range(4)])
        check("COV 4 표본: 폴트 아님", r.get("sim_fault") == 0)
        r = sim_rows([sim_row(4, v1=4.3)])
        check("COV 5 표본: 폴트", r.get("sim_fault") == 1 and r.get("sim_bits") == 1, f"bits={r.get('sim_bits')}")
        r = sim_rows([sim_row(5)])
        check("정상값 복귀해도 래치 유지", r.get("sim_fault") == 1)
        r = mcu("SIM CLEAR")
        check("SIM CLEAR 로 해제", r.get("sim_fault") == 0)
        r = sim_rows([sim_row(k, v2=2.7) for k in range(9)])
        check("CUV 9 표본: 폴트 아님", r.get("sim_fault") == 0)
        r = sim_rows([sim_row(9, v2=2.7)])
        check("CUV 10 표본: 폴트", r.get("sim_bits") == 2, f"bits={r.get('sim_bits')}")
        mcu("SIM CLEAR")
        r = sim_rows([sim_row(0, i=-6.0)])
        check("-6.0 A: 폴트 아님", r.get("sim_fault") == 0)
        r = sim_rows([sim_row(1, i=-6.1)])
        check("-6.1 A: 폴트 (OCD)", r.get("sim_bits") == 4, f"bits={r.get('sim_bits')}")
        mcu("SIM CLEAR")
        r = sim_rows([sim_row(k, t1=60.5) for k in range(10)])
        check("OT 10 표본: 폴트", r.get("sim_bits") == 8, f"bits={r.get('sim_bits')}")
    finally:
        r = mcu("SIM CLEAR")
        check("끝난 뒤 폴트 없음", r.get("sim_fault") == 0)
    try:
        q = http(AGENT + "/state")
        check("패널 쪽 MCU 상태 읽기", "sim" in q["pcb"])
    except (OSError, KeyError) as e:
        check("패널 쪽 MCU 상태 읽기", False, e)


def test_scenario() -> None:
    section("8. 시나리오 전체 경로 (Soft ISC 휴지 20 초)")
    p = panel()
    if not p or p.get("matlab") != "ready":
        skip("시나리오", "패널 또는 MATLAB 준비 안 됨")
        return
    time.sleep(1.5)
    r = http(PANEL + "/api/start", {"scenario": "isc_rest", "cell": 2})
    if not check("패널 시작", bool(r.get("ok")), r.get("error", "")):
        return
    t0, ae, fault, bits, cell, phase, sent = None, None, None, 0, None, "", 0
    base = time.time()
    try:
        while time.time() - base < 70:
            s = (panel().get("session") or {})
            phase, sent = s.get("phase", ""), s.get("sent", 0)
            m = monitor()
            if phase in ("send", "done") and t0 is None:
                t0 = time.time()
            if t0 is not None and m:
                now = time.time() - t0
                if m.get("status") == "ABNORMAL" and ae is None:
                    ae, cell = now, m.get("fault_cell")
                if m["pcb"].get("sim_fault") and fault is None:
                    fault, bits = now, m["pcb"].get("sim_bits")
            if phase in ("done", "idle") and t0 is not None:
                break
            time.sleep(0.25)
        if not check("프레임 전송", phase == "done" and sent == 200, f"{sent} 개, 단계 {phase}"):
            return
        q = http(AGENT + "/state")["pcb"]["sim"]
        check("SIM 전달", q.get("ok") == 200 and not q.get("rejected"), q)
        check("AE 경보", ae is not None and ae <= AE_MAX_S, f"{ae:.1f} s, 셀 {cell}" if ae is not None else "경보 없음")
        check("MCU 폴트", fault is not None and fault <= MCU_MAX_S and bits == 2, f"{fault:.1f} s, bits {bits}" if fault is not None else "폴트 없음")
        check("AE 가 MCU 보다 먼저", ae is not None and fault is not None and ae < fault)
    finally:
        http(PANEL + "/api/stop", {})
        time.sleep(5)
        m = monitor()
        check("중지 후 정상 모드 복귀, 폴트 해제", m.get("mode") == "normal" and not m.get("pcb", {}).get("sim_fault"), m.get("mode"))


def safe_stop() -> None:
    for action in ("motor_off", "charge_off"):
        try:
            button(action)
        except OSError:
            pass
    st = mcu("STATUS")
    if st.get("stage") or st.get("charge_on"):
        print(f"\n  [경고] 출력이 꺼지지 않았습니다: stage={st.get('stage')}, charge_on={st.get('charge_on')}. ESTOP 으로 정지하세요.", flush=True)


def test_motor() -> None:
    section("9. 모터 실구동 (확인 후)")
    if not ask("모터를 실제로 돌립니다. 바퀴를 띄웠고 주변이 안전한가요?"):
        skip("모터", "확인하지 않음")
        return
    try:
        r = button("motor_on")
        check("모터 ON 버튼", bool(r.get("ok")), r.get("msg", ""))
        time.sleep(3)
        s = monitor()
        p = s["pcb"]
        check("모터 구동 (stage 3, 90 %/30 %)", p["stage"] == 3 and p["m1"] == 900 and p["m2"] == 300, f"m1={p['m1']}, m2={p['m2']}")
        i = (s.get("latest") or {}).get("i")
        check("모터 전류", i is not None and i < I_MOTOR_A, f"{i} A")
        check("모터 ON 은 정상주행 표시 안 켬", not s.get("drive_active"))
        r = button("motor_off")
        time.sleep(1.2)
        p = monitor()["pcb"]
        check("모터 OFF, 센싱 유지", bool(r.get("ok")) and p["stage"] == 0 and p["started"] == 1)
        r = button("drive_start")
        time.sleep(1.5)
        s = monitor()
        check("정상주행 시작 (표시 켜짐)", bool(r.get("ok")) and bool(s.get("drive_active")) and s["pcb"]["stage"] == 3, r.get("msg", ""))
        mcu("SIM CLEAR")
        sim_rows([sim_row(k, v1=4.3) for k in range(5)])
        time.sleep(1.5)
        p = monitor()["pcb"]
        check("폴트 시 MCU 가 모터 차단", p["sim_fault"] == 1 and p["stage"] == 0 and p["remote"] == -44, f"stage={p['stage']}, remote={p['remote']}")
        r = button("motor_on")
        check("폴트 중 ON 거부", not r.get("ok"), r.get("msg", ""))
        mcu("SIM CLEAR")
        time.sleep(1.2)
        p = monitor()["pcb"]
        check("해제 후 자동 재기동 없음", p["sim_fault"] == 0 and p["stage"] == 0)
        r = button("drive_stop")
        time.sleep(1.2)
        p = monitor()["pcb"]
        check("정상주행 정지 (STOP)", bool(r.get("ok")) and p["started"] == 0 and p["stage"] == 0)
    except (OSError, KeyError, TypeError) as e:
        check("모터 시험", False, e)
    finally:
        mcu("SIM CLEAR")
        safe_stop()


def test_charge() -> None:
    section("10. 충전 실구동 (확인 후)")
    if not ask("충전을 실제로 켭니다. 충전기를 연결했나요?"):
        skip("충전", "확인하지 않음")
        return
    try:
        r = button("charge_on")
        if not check("충전 ON 버튼", bool(r.get("ok")), r.get("msg", "")):
            return
        time.sleep(3)
        s = monitor()
        i = (s.get("latest") or {}).get("i")
        check("충전 출력", s["pcb"]["charge_on"] == 1 and s.get("panel") == "charge")
        check("충전 전류", i is not None and i > I_CHARGE_A, f"{i} A")
        r = button("motor_on")
        check("충전 중 모터 거부", not r.get("ok"), r.get("msg", ""))
        r = button("charge_off")
        time.sleep(1.2)
        check("충전 OFF", bool(r.get("ok")) and monitor()["pcb"]["charge_on"] == 0)
    except (OSError, KeyError, TypeError) as e:
        check("충전 시험", False, e)
    finally:
        safe_stop()


def main() -> int:
    print("AI-BMS 전체 점검" + (" (읽기 전용)" if READONLY else ""), flush=True)
    was_started = None
    pi_ok = test_link()
    mcu_up = False
    if pi_ok:
        test_services()
        _, mcu_up = test_ui()
    else:
        skip("Pi 서비스·UI", "Pi 연결 없음")
    fw_ok = test_firmware() if mcu_up else False
    if not fw_ok:
        section("4. MCU 펌웨어")
        skip("펌웨어와 실제 시험", "MCU 응답 없음")
    st = mcu("STATUS") if fw_ok else {}
    p = panel()
    in_session = bool(p) and (p.get("session") or {}).get("phase") not in (None, "idle")
    test_matlab(idle=not in_session)
    actuated = bool(st.get("stage") or st.get("charge_on") or st.get("charge_req") or st.get("sim_active"))
    reason = ("--readonly" if READONLY else "MCU 응답 없음" if not fw_ok else "시나리오 진행 중" if in_session
              else "모터·충전 또는 시뮬레이션이 켜져 있음" if actuated else "")
    if reason:
        section("6~10. 실제 시험")
        skip("센싱, MCU 판정, 시나리오, 실구동", reason)
    else:
        was_started = bool(st.get("started"))
        try:
            test_sensing()
            test_judge()
            test_scenario()
            test_motor()
            test_charge()
        except KeyboardInterrupt:
            print("\n  중단됨", flush=True)
            report("FAIL", "사용자가 중단", "")
        except OSError as e:
            report("FAIL", "실제 시험 중단", e)
        finally:
            safe_stop()
            mcu("SIM CLEAR")
            if not was_started:
                try:
                    button("drive_stop")
                except OSError:
                    pass
            end = mcu("STATUS")
            section("정리")
            check("모든 출력 꺼짐", not end.get("stage") and not end.get("charge_on") and not end.get("gpio", 0) & 4, f"stage={end.get('stage')}, gpio={end.get('gpio')}")
            check("폴트 해제", not end.get("sim_fault"))
            check("센싱 원래대로", bool(end.get("started")) == was_started, f"started={end.get('started')}")
    n = {k: sum(1 for r in results if r[0] == k) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\n===== 결과: PASS {n['PASS']}, FAIL {n['FAIL']}, SKIP {n['SKIP']} =====", flush=True)
    for state, name, info in results:
        if state == "FAIL":
            print(f"  FAIL  {name}" + (f"  ({info})" if info else ""), flush=True)
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n중단됨. 출력 상태를 확인하세요.", flush=True)
        sys.exit(130)
PY
