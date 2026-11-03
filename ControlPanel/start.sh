#!/bin/zsh
ROOT=${0:A:h}
AGENT="$ROOT/../AI_AutoEncoder(RaspberryPi)/RaspberryPi/control_panel/pi_agent.py"
PI=rpi4@10.55.0.2
mkdir -p $ROOT/runtime
if scp -q -o BatchMode=yes -o ConnectTimeout=5 "$AGENT" $PI:/tmp/pi_agent.py; then
  ssh -o BatchMode=yes $PI 'D="/home/rpi4/2026ESWContest_mobility_kimdongwooteam/AI_AutoEncoder(RaspberryPi)/RaspberryPi/control_panel"; mkdir -p $D; mv /tmp/pi_agent.py $D/; [ -f $D/agent.pid ] && kill $(cat $D/agent.pid) 2>/dev/null; sleep 0.5; cd $D; nohup python3 pi_agent.py > agent.log 2>&1 & echo $! > $D/agent.pid'
  echo "Pi 에이전트 실행 (10.55.0.2:8061)"
else
  echo "[경고] 라즈베리파이에 연결하지 못했습니다. USB 연결을 확인한 뒤 start.sh 를 다시 실행해 주세요."
fi
[ -f $ROOT/runtime/server.pid ] && kill $(cat $ROOT/runtime/server.pid) 2>/dev/null
cd $ROOT
nohup python3 panel_server.py >> runtime/server.log 2>&1 &
echo $! > runtime/server.pid
sleep 2
open http://127.0.0.1:8060/
echo "패널: http://127.0.0.1:8060  (처음에는 MATLAB 준비에 약 1 분 걸립니다)"
