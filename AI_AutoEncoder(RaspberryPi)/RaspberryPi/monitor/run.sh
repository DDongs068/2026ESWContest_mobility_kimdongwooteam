#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PI=rpi4@10.55.0.2
PI_DIR="/home/rpi4/2026ESWContest_mobility_kimdongwooteam/AI_AutoEncoder(RaspberryPi)/RaspberryPi/monitor"
if ! grep -qs "Raspberry Pi" /proc/device-tree/model; then
  ssh -n -o BatchMode=yes -o ConnectTimeout=5 "$PI" "bash '$PI_DIR/run.sh'" || { echo "[오류] 라즈베리파이에 연결하지 못했습니다. USB 연결을 확인해 주세요."; exit 1; }
  open "http://10.55.0.2:8050"
  exit 0
fi
cd "$DIR" && mkdir -p runtime
pkill -f "[m]onitor_server.py" 2>/dev/null; sleep 1
nohup python3 monitor_server.py > runtime/monitor.log 2>&1 &
sleep 4
ss -ltn | grep -q ":8050 " && echo "AE 모니터 실행 중: http://10.55.0.2:8050  (입력 버퍼 시트 /buffer)" || { echo "[오류] 모니터 시작 실패"; tail -5 runtime/monitor.log; exit 1; }
