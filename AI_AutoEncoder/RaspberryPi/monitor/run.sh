#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" && mkdir -p runtime
pkill -f "[m]onitor_server.py" 2>/dev/null; sleep 1
nohup python3 monitor_server.py > runtime/monitor.log 2>&1 &
sleep 4
ss -ltn | grep -q ":8050 " && echo "AE 모니터 실행 중: http://<Pi IP>:8050  (입력 버퍼 시트 /buffer)" || { echo "[오류] 모니터 시작 실패"; tail -5 runtime/monitor.log; }
