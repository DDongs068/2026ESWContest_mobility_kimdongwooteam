#!/bin/zsh
ROOT=${0:A:h}
[ -f $ROOT/runtime/server.pid ] && kill $(cat $ROOT/runtime/server.pid) 2>/dev/null && rm -f $ROOT/runtime/server.pid
touch $ROOT/runtime/worker.quit
# Pi 계정이 pi 가 아니면 pi 와 /home/pi 를 바꿔 주세요.
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@10.55.0.2 'D="/home/pi/2026ESWContest_mobility_kimdongwooteam/AI_AutoEncoder(RaspberryPi)/RaspberryPi/control_panel"; [ -f $D/agent.pid ] && kill $(cat $D/agent.pid) 2>/dev/null; rm -f $D/agent.pid' 2>/dev/null
echo "종료했습니다."
