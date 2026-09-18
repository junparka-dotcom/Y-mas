#!/bin/bash
mkdir -p ~/logs
WATCHDOG_LOG=~/logs/watchdog.log

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$WATCHDOG_LOG"
}

log "=== Watchdog 시작 ==="

# 종료 시 자식 프로세스까지 정리
cleanup() {
    log "Watchdog 종료 신호 수신, 정리 중..."
    pkill -9 ir_depth_bridge 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

while true; do
    # 브릿지가 안 떠 있으면 실행
    if ! pgrep -x ir_depth_bridge > /dev/null; then
        log "브릿지가 실행 중이 아님 -- 시작"
        rm -f /tmp/ymas_irdepth.sock
        nohup ~/ir_depth_bridge >> ~/logs/bridge.log 2>&1 &
        sleep 3   # 워밍업 대기
    fi
    sleep 2   # 2초마다 상태 확인
done
