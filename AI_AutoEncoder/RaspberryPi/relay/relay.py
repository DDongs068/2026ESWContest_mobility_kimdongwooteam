#!/usr/bin/env python3
import argparse
import csv
import ipaddress
import os
import selectors
import socket
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    p = argparse.ArgumentParser(description="Mac <-> PCB UDP relay")
    p.add_argument("--listen-port", type=int, default=int(os.environ.get("RELAY_LISTEN_PORT", 6000)))
    p.add_argument("--allow-net", default=os.environ.get("RELAY_ALLOW_NET", "10.55.0.0/24"),
                   help="이 대역에서 온 패킷만 PCB로 전달 (기본: USB 쪽만)")
    p.add_argument("--pcb-ip", default=os.environ.get("RELAY_PCB_IP", "192.168.10.20"))
    p.add_argument("--pcb-port", type=int, default=int(os.environ.get("RELAY_PCB_PORT", 5000)))
    p.add_argument("--log-dir", default=os.environ.get("RELAY_LOG_DIR", os.path.join(HERE, "logs")))
    return p.parse_args()


def show(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
        if text.isprintable() or all(c.isprintable() or c in "\r\n\t" for c in text):
            return text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    except UnicodeDecodeError:
        pass
    return "hex:" + data.hex(" ")


class Logger:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.day = None
        self.f = None
        self.w = None

    def write(self, direction, src, dst, data, note=""):
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        if day != self.day:
            if self.f:
                self.f.close()
            path = os.path.join(self.log_dir, f"relay_{day}.csv")
            new = not os.path.exists(path)
            self.f = open(path, "a", newline="", encoding="utf-8")
            self.w = csv.writer(self.f)
            if new:
                self.w.writerow(["time", "direction", "src", "dst", "bytes", "payload", "note"])
            self.day = day
        ts = now.isoformat(timespec="milliseconds")
        self.w.writerow([ts, direction, src, dst, len(data), show(data), note])
        self.f.flush()
        print(f"{ts} {direction:9} {src} -> {dst} [{len(data)}B] {show(data)} {note}".rstrip(), flush=True)


def main():
    a = parse_args()
    allow = ipaddress.ip_network(a.allow_net)
    pcb = (a.pcb_ip, a.pcb_port)
    log = Logger(a.log_dir)

    mac_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mac_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    mac_sock.bind(("0.0.0.0", a.listen_port))

    pcb_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pcb_sock.bind(("0.0.0.0", 0))

    sel = selectors.DefaultSelector()
    sel.register(mac_sock, selectors.EVENT_READ, "mac")
    sel.register(pcb_sock, selectors.EVENT_READ, "pcb")

    last_mac = None
    print(f"relay: UDP :{a.listen_port} (from {allow}) -> PCB {pcb[0]}:{pcb[1]}, "
          f"PCB 응답은 Mac으로 반환, log={a.log_dir}", flush=True)

    while True:
        for key, _ in sel.select():
            sock = key.fileobj
            try:
                data, addr = sock.recvfrom(65535)
            except ConnectionRefusedError:
                log.write("pcb-err", f"{pcb[0]}:{pcb[1]}", "-", b"", "PCB 포트 닫힘(ICMP unreachable)")
                continue
            src = f"{addr[0]}:{addr[1]}"

            if key.data == "mac":
                if ipaddress.ip_address(addr[0]) not in allow:
                    log.write("drop", src, "-", data, f"허용 대역({allow}) 밖")
                    continue
                last_mac = addr
                try:
                    pcb_sock.sendto(data, pcb)
                    log.write("mac->pcb", src, f"{pcb[0]}:{pcb[1]}", data)
                except OSError as e:
                    log.write("mac->pcb", src, f"{pcb[0]}:{pcb[1]}", data, f"전송 실패: {e}")
            else:
                if addr[0] != pcb[0]:
                    log.write("drop", src, "-", data, "PCB가 아닌 곳에서 온 응답")
                    continue
                if last_mac is None:
                    log.write("pcb->mac", src, "-", data, "아직 Mac 주소 모름, 전달 안 함")
                    continue
                try:
                    mac_sock.sendto(data, last_mac)
                    log.write("pcb->mac", src, f"{last_mac[0]}:{last_mac[1]}", data)
                except OSError as e:
                    log.write("pcb->mac", src, f"{last_mac[0]}:{last_mac[1]}", data, f"전송 실패: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
