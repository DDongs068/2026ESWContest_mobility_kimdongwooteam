#!/usr/bin/env python3
import argparse
import csv
import socket
import time

FIELDS = ["seq", "ms", "valid", "V1", "V2", "V3", "V4",
          "T1", "T2", "T3", "T4", "I", "STACK", "PACK", "LD"]


def main():
    p = argparse.ArgumentParser(description="abnormal CSV -> Pi UDP 10Hz")
    p.add_argument("csv")
    p.add_argument("--pi", default="10.55.0.2:5002")
    p.add_argument("--hz", type=float, default=10.0)
    p.add_argument("--loop", action="store_true", help="끝나면 처음부터 다시 (seq 는 계속 증가)")
    p.add_argument("--keep-time", action="store_true", help="seq, ms 를 CSV 값 그대로 보냄")
    a = p.parse_args()

    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    missing = [k for k in FIELDS if rows and k not in rows[0]]
    if not rows or missing:
        raise SystemExit(f"CSV 열 부족: {missing or '데이터 없음'}")

    host, port = a.pi.rsplit(":", 1)
    dst = (host, int(port))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dt = 1.0 / a.hz
    print(f"{a.csv} ({len(rows)}줄) -> {dst[0]}:{dst[1]} {a.hz:g} Hz", flush=True)

    n, t_next = 0, time.monotonic()
    while True:
        for r in rows:
            vals = [r[k].strip() for k in FIELDS]
            if not a.keep_time:
                vals[0], vals[1] = str(n + 1), str(int(round(n * dt * 1000)))
            sock.sendto((",".join(vals) + "\n").encode(), dst)
            n += 1
            if n % 100 == 0:
                print(f"{n}줄 전송", flush=True)
            t_next += dt
            time.sleep(max(0.0, t_next - time.monotonic()))
        if not a.loop:
            break
    print(f"끝: {n}줄", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
