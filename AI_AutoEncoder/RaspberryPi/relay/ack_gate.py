#!/usr/bin/env python3
import argparse
import csv
import ipaddress
import os
import select
import socket
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
N_FIELDS = 15


def parse_args():
    p = argparse.ArgumentParser(description="Mac -> PCB -> ACK -> buffer gate")
    p.add_argument("--listen-port", type=int, default=5002, help="Mac 에서 받는 포트")
    p.add_argument("--allow-net", default="10.55.0.0/24", help="이 대역(USB)에서 온 것만 받음")
    p.add_argument("--pcb", default="192.168.10.20:5002", help="PCB 주소:포트")
    p.add_argument("--src-port", type=int, default=5003, help="PCB 로 보낼 때 Pi 출발 포트 (ACK 받는 포트)")
    p.add_argument("--buffer", default="127.0.0.1:5013", help="ACK 확인된 줄을 넘길 곳 (receiver.py 비정상)")
    p.add_argument("--ack-timeout", type=float, default=0.2, help="ACK 기다리는 시간(초)")
    p.add_argument("--log-dir", default=os.path.join(HERE, "data_abnormal_gate"))
    p.add_argument("--stats-every", type=float, default=10.0)
    return p.parse_args()


def addr(s):
    host, port = s.rsplit(":", 1)
    return host, int(port)


class Log:
    def __init__(self, d):
        os.makedirs(d, exist_ok=True)
        self.d, self.day, self.f, self.w = d, None, None, None

    def write(self, event, seq, tries="", rtt_ms="", note=""):
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        if day != self.day:
            if self.f:
                self.f.close()
            path = os.path.join(self.d, f"gate_{day}.csv")
            new = not os.path.exists(path)
            self.f = open(path, "a", newline="", encoding="utf-8")
            self.w = csv.writer(self.f)
            if new:
                self.w.writerow(["pi_time", "event", "seq", "tries", "rtt_ms", "note"])
            self.day = day
        self.w.writerow([now.isoformat(timespec="milliseconds"), event, seq, tries, rtt_ms, note])
        self.f.flush()


def main():
    a = parse_args()
    allow = ipaddress.ip_network(a.allow_net)
    pcb, buf = addr(a.pcb), addr(a.buffer)
    log = Log(a.log_dir)

    mac_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mac_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    mac_sock.bind(("0.0.0.0", a.listen_port))
    pcb_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pcb_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    pcb_sock.bind(("0.0.0.0", a.src_port))
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"ack_gate: Mac :{a.listen_port} (from {allow}) -> PCB {pcb[0]}:{pcb[1]} (src :{a.src_port}) "
          f"-> ACK,<seq> -> buffer {buf[0]}:{buf[1]}, timeout {a.ack_timeout * 1000:.0f} ms, 재전송 1회", flush=True)

    pending = {}
    done = {}
    st = dict(rx=0, sent=0, resent=0, ack=0, unconf=0, late=0, bad=0)
    rtts, t_stats = [], time.monotonic()

    def send(seq, line):
        try:
            pcb_sock.sendto(line.encode(), pcb)
        except OSError as e:
            log.write("send_error", seq, note=str(e))

    while True:
        r, _, _ = select.select([mac_sock, pcb_sock], [], [], 0.01)
        now = time.monotonic()

        if mac_sock in r:
            data, src = mac_sock.recvfrom(65535)
            if ipaddress.ip_address(src[0]) in allow:
                for line in data.decode("utf-8", "replace").splitlines():
                    line = line.strip()
                    cols = line.split(",")
                    if not line or line.lower().startswith("seq"):
                        continue
                    try:
                        seq = int(cols[0])
                    except ValueError:
                        st["bad"] += 1
                        continue
                    if len(cols) != N_FIELDS:
                        st["bad"] += 1
                        log.write("bad_format", seq, note=f"열 {len(cols)}개")
                        continue
                    st["rx"] += 1
                    if seq in pending:
                        log.write("unconfirmed", seq, pending[seq][3], note="같은 seq 새로 들어옴")
                        st["unconf"] += 1
                    pending[seq] = [line, now, now, 1]
                    done.pop(seq, None)
                    send(seq, line)
                    st["sent"] += 1
                    log.write("sent", seq, 1)

        if pcb_sock in r:
            try:
                data, src = pcb_sock.recvfrom(65535)
            except ConnectionRefusedError:
                data, src = b"", None
            if src and src[0] == pcb[0]:
                for msg in data.decode("utf-8", "replace").splitlines():
                    p = msg.strip().split(",")
                    if len(p) < 2 or p[0].strip().upper() != "ACK":
                        continue
                    try:
                        seq = int(p[1])
                    except ValueError:
                        continue
                    if seq in pending:
                        line, t0, _, tries = pending.pop(seq)
                        rtt = (now - t0) * 1000
                        out.sendto(line.encode(), buf)
                        st["ack"] += 1
                        rtts.append(rtt)
                        done[seq] = now
                        log.write("ack", seq, tries, f"{rtt:.1f}")
                    elif seq in done:
                        log.write("dup_ack", seq)
                    else:
                        st["late"] += 1
                        log.write("late_ack", seq, note="이미 미확인 처리된 seq")

        for seq in [s for s, v in pending.items() if now - v[2] >= a.ack_timeout]:
            line, t0, _, tries = pending[seq]
            if tries == 1:
                pending[seq][2:] = [now, 2]
                send(seq, line)
                st["resent"] += 1
                log.write("resent", seq, 2)
            else:
                del pending[seq]
                st["unconf"] += 1
                log.write("unconfirmed", seq, 2, note="ACK 없음, 버퍼에 안 넣음")
        if len(done) > 5000:
            for s in [s for s, t in done.items() if now - t > 30]:
                del done[s]

        if now - t_stats >= a.stats_every:
            med = sorted(rtts)[len(rtts) // 2] if rtts else None
            print(f"stats: 받음 {st['rx']}, 보냄 {st['sent']}, 재전송 {st['resent']}, ACK {st['ack']}, "
                  f"미확인 {st['unconf']}, 늦은ACK {st['late']}, 형식오류 {st['bad']}, 대기 {len(pending)}"
                  + (f", 왕복 중앙 {med:.1f} ms" if med is not None else ""), flush=True)
            rtts, t_stats = [], now


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
