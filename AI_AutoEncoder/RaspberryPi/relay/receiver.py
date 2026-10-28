#!/usr/bin/env python3
import argparse
import csv
import ipaddress
import os
import socket
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = ["seq", "ms", "valid", "V1", "V2", "V3", "V4",
          "T1", "T2", "T3", "T4", "I", "STACK", "PACK", "LD"]


def parse_args():
    p = argparse.ArgumentParser(description="PCB -> Pi data receiver")
    p.add_argument("--port", type=int, default=int(os.environ.get("RECV_PORT", 5001)))
    p.add_argument("--allow-net", default=os.environ.get("RECV_ALLOW_NET", "192.168.10.0/24"),
                   help="이 대역에서 온 패킷만 저장 (기본: PCB 쪽 이더넷)")
    p.add_argument("--data-dir", default=os.environ.get("RECV_DATA_DIR", os.path.join(HERE, "data")))
    p.add_argument("--stats-every", type=float, default=10.0, help="상태 출력 주기(초)")
    p.add_argument("--forward", default=os.environ.get("RECV_FORWARD", "127.0.0.1:5011"),
                   help="정상 줄을 이 주소로 그대로 넘김 (엑셀 표 프로그램용, 빈 값이면 안 넘김)")
    return p.parse_args()


class DailyCsv:
    def __init__(self, data_dir, prefix, header):
        self.data_dir, self.prefix, self.header = data_dir, prefix, header
        os.makedirs(data_dir, exist_ok=True)
        self.day = self.f = self.w = None

    def write(self, row):
        day = datetime.now().strftime("%Y%m%d")
        if day != self.day:
            if self.f:
                self.f.close()
            path = os.path.join(self.data_dir, f"{self.prefix}_{day}.csv")
            new = not os.path.exists(path)
            self.f = open(path, "a", newline="", encoding="utf-8")
            self.w = csv.writer(self.f)
            if new:
                self.w.writerow(self.header)
            self.day = day
        self.w.writerow(row)
        self.f.flush()


def main():
    a = parse_args()
    allow = ipaddress.ip_network(a.allow_net)
    data = DailyCsv(a.data_dir, "bms", ["pi_time"] + FIELDS + ["lost", "note"])
    bad = DailyCsv(a.data_dir, "bad", ["pi_time", "src", "reason", "raw"])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", a.port))
    sock.settimeout(1.0)
    fwd = None
    if a.forward:
        host, port = a.forward.rsplit(":", 1)
        fwd = (host, int(port))
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"receiver: UDP :{a.port} (from {allow}) -> {a.data_dir}/bms_YYYYMMDD.csv"
          + (f", forward -> {fwd[0]}:{fwd[1]}" if fwd else ""), flush=True)

    last_seq = None
    n_rows = n_lost = n_bad = 0
    window_rows, t_stats = 0, time.monotonic()
    last_row = None

    while True:
        try:
            payload, addr = sock.recvfrom(65535)
        except socket.timeout:
            payload = None

        if payload is not None:
            now = datetime.now().isoformat(timespec="milliseconds")
            src = f"{addr[0]}:{addr[1]}"
            if ipaddress.ip_address(addr[0]) not in allow:
                bad.write([now, src, "허용 대역 밖", payload[:200].decode("utf-8", "replace")])
                n_bad += 1
                continue

            for line in payload.decode("utf-8", "replace").splitlines():
                line = line.strip().rstrip("\x00")
                if not line or line.lower().startswith("seq"):
                    continue
                cols = [c.strip() for c in line.split(",")]
                if len(cols) != len(FIELDS):
                    bad.write([now, src, f"열 개수 {len(cols)} (기대 {len(FIELDS)})", line])
                    n_bad += 1
                    continue
                try:
                    seq = int(cols[0])
                except ValueError:
                    bad.write([now, src, "seq가 정수가 아님", line])
                    n_bad += 1
                    continue

                lost, note = 0, ""
                if last_seq is not None:
                    if seq > last_seq + 1:
                        lost = seq - last_seq - 1
                    elif seq <= last_seq:
                        note = f"seq 되돌아감 {last_seq}->{seq} (PCB 재시작?)"
                last_seq = seq
                n_rows += 1
                n_lost += lost
                window_rows += 1
                last_row = cols
                data.write([now] + cols + [lost, note])
                if fwd:
                    try:
                        out.sendto(",".join(cols).encode(), fwd)
                    except OSError:
                        pass
                if note or lost:
                    print(f"{now} seq={seq} lost={lost} {note}".rstrip(), flush=True)

        t = time.monotonic()
        if t - t_stats >= a.stats_every:
            rate = window_rows / (t - t_stats)
            latest = dict(zip(FIELDS, last_row)) if last_row else {}
            print(f"stats: {rate:.1f} Hz, 총 {n_rows}줄, 유실 {n_lost}, 형식오류 {n_bad}"
                  + (f", 최신 {latest}" if latest else ""), flush=True)
            window_rows, t_stats = 0, t


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
