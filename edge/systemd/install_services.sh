#!/bin/bash
# Y-mas systemd 서비스 설치 스크립트 (AGX Orin, 무인 운영)
#
#   ymas-bridge.service   : Orbbec C++ 브리지 (카메라 캡처)
#   ymas-tier3.service    : Tier3 간호 스테이션 알림 서버 (FastAPI, :8000)
#   ymas-realtime.service : 헤드리스 추론 (낙상 판정 → Tier3 알림)
# 를 시스템 서비스로 등록하고 부팅 자동시작을 켠다.
#
# 사전 조건(배포 가이드 1~6 단계 완료):
#   - ~/Y-mas/edge/bridges/ir_depth_bridge  (컴파일됨)
#   - ~/Y-mas/edge/ymas_realtime_ir.py + 모델/엔진 파일
#   - Orbbec udev 규칙 설치됨
#   - Tier3 서버 의존성:  pip install --user -r ~/Y-mas/tier3/requirements-tier3.txt
#
# 사용법:  sudo bash install_services.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR=/etc/systemd/system

if [[ $EUID -ne 0 ]]; then
    echo "!! sudo 로 실행하세요:  sudo bash install_services.sh" >&2
    exit 1
fi

echo "[1/4] 사전 조건 확인"
BRIDGE_BIN=/home/ymas/Y-mas/edge/bridges/ir_depth_bridge
RT_PY=/home/ymas/Y-mas/edge/ymas_realtime_ir.py
TIER3_APP=/home/ymas/Y-mas/tier3/server/app.py
[[ -x "$BRIDGE_BIN" ]] || { echo "!! 브리지 실행파일 없음: $BRIDGE_BIN (5단계 컴파일 먼저)" >&2; exit 1; }
[[ -f "$RT_PY" ]]      || { echo "!! 실시간 스크립트 없음: $RT_PY" >&2; exit 1; }
[[ -f "$TIER3_APP" ]]  || { echo "!! Tier3 서버 없음: $TIER3_APP" >&2; exit 1; }
# fastapi/uvicorn 설치 여부 경고(설치는 사용자가)
sudo -u ymas python3 -c "import fastapi, uvicorn" 2>/dev/null \
    || echo "   (경고) fastapi/uvicorn 미설치 → sudo -u ymas pip install --user -r /home/ymas/Y-mas/tier3/requirements-tier3.txt"

echo "[2/4] 유닛 파일 복사 → $UNIT_DIR"
cp "$SCRIPT_DIR/ymas-bridge.service"   "$UNIT_DIR/"
cp "$SCRIPT_DIR/ymas-tier3.service"    "$UNIT_DIR/"
cp "$SCRIPT_DIR/ymas-realtime.service" "$UNIT_DIR/"

echo "[3/4] systemd reload + enable"
systemctl daemon-reload
systemctl enable ymas-bridge.service ymas-tier3.service ymas-realtime.service

echo "[4/4] 서비스 시작"
systemctl restart ymas-bridge.service
systemctl restart ymas-tier3.service
sleep 3
systemctl restart ymas-realtime.service

echo
echo "완료. 상태 확인:"
echo "  systemctl status ymas-bridge ymas-tier3 ymas-realtime"
echo "  journalctl -u ymas-realtime -f      # 실시간 판정/Tier3 알림 로그"
echo "  journalctl -u ymas-tier3 -f         # 알림 서버 로그"
echo "  대시보드:  http://<오린IP>:8000  (오린 로컬이면 http://localhost:8000)"
echo
echo "제거:  sudo systemctl disable --now ymas-realtime ymas-tier3 ymas-bridge"
