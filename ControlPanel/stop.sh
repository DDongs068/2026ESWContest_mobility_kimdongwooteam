#!/bin/zsh
ROOT=${0:A:h}
[ -f $ROOT/runtime/server.pid ] && kill $(cat $ROOT/runtime/server.pid) 2>/dev/null && rm -f $ROOT/runtime/server.pid
touch $ROOT/runtime/worker.quit
ssh -o BatchMode=yes -o ConnectTimeout=5 rpi4@10.55.0.2 'D="/home/rpi4/2026ESWContest_mobility_kimdongwooteam/AI_AutoEncoder(RaspberryPi)/RaspberryPi/control_panel"; [ -f $D/agent.pid ] && kill $(cat $D/agent.pid) 2>/dev/null; rm -f $D/agent.pid' 2>/dev/null
echo "종료했습니다."
