#!/usr/bin/env python3
"""Mock Tier 2 송신기 — 하드웨어 없이 Tier 3 대시보드 검증용.

실제 Tier 2(카메라 + ST-GCN)가 없으므로, 낙상/정상 시나리오를 흉내 내어
서버 /ingest 로 이벤트를 POST 한다. 실제 Tier 2 는 나중에 동일한 스키마
(schema.FallEvent)로 이 엔드포인트에 붙이면 된다.

사용법:
    python tier3/mock_tier2.py --server http://localhost:8000 --scenario fall --bed 301-A
    python tier3/mock_tier2.py --scenario multi     # 여러 침대 혼합
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import FallEvent, Severity  # noqa: E402


def post(server: str, event: FallEvent):
    data = json.dumps(event.to_dict()).encode("utf-8")
    req = urllib.request.Request(server.rstrip("/") + "/ingest", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def send_normal(server, bed, total=68.0):
    return FallEvent(bed_id=bed, severity=Severity.NORMAL.value, source="tier1",
                     total_kg=total, cog_x=850.0, cog_y=280.0, edge_ratio=0.02,
                     p_normal=0.95, p_risk=0.03, p_fall=0.02)


def scenario_fall(server, bed):
    """정상 → 가장자리 접근(주의/위험) → 낙상 확정."""
    print(f"[{bed}] 정상 모니터링…")
    for _ in range(3):
        post(server, send_normal(server, bed)); time.sleep(0.6)

    print(f"[{bed}] 가장자리 접근 — CAUTION")
    post(server, FallEvent(bed_id=bed, severity=Severity.CAUTION.value, source="tier1",
                           total_kg=67.5, cog_x=850, cog_y=360, edge_ratio=0.42,
                           reason="edge_approach")); time.sleep(0.7)

    print(f"[{bed}] 임계 근접 — DANGER")
    post(server, FallEvent(bed_id=bed, severity=Severity.DANGER.value, source="tier1",
                           total_kg=66.0, cog_x=850, cog_y=430, edge_ratio=0.63,
                           reason="edge_danger")); time.sleep(0.5)

    print(f"[{bed}] >>> 낙상 확정 (Tier 2) — FALL")
    post(server, FallEvent(bed_id=bed, severity=Severity.FALL.value, source="tier2",
                           reason="FASTPATH_WEIGHT_DROP",
                           p_normal=0.05, p_risk=0.08, p_fall=0.91,
                           tilt_final=72.0, zone_status="이탈", exit_tag="[EXIT+TILT]",
                           total_kg=42.0, cog_x=850, cog_y=470, edge_ratio=0.70))
    print(f"[{bed}] 낙상 이벤트 전송 완료")


def scenario_normal(server, bed):
    print(f"[{bed}] 정상 시나리오 (낙상 없음)")
    for i in range(8):
        post(server, send_normal(server, bed, total=68.0 + (i % 3) * 0.2))
        time.sleep(0.5)
    print(f"[{bed}] 완료 — 대시보드에 FALL 경보가 없어야 정상")


def scenario_multi(server):
    """여러 침대 혼합: 두 침대 정상, 한 침대 낙상."""
    for bed in ("301-A", "301-B", "302-A"):
        post(server, send_normal(server, bed)); time.sleep(0.2)
    print("3개 침대 정상 등록")
    time.sleep(1.0)
    scenario_fall(server, "302-A")


def main():
    ap = argparse.ArgumentParser(description="Mock Tier 2 송신기")
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--scenario", default="fall",
                    choices=["fall", "normal", "multi"])
    ap.add_argument("--bed", default="301-A")
    args = ap.parse_args()

    print(f"서버: {args.server} / 시나리오: {args.scenario}")
    try:
        if args.scenario == "fall":
            scenario_fall(args.server, args.bed)
        elif args.scenario == "normal":
            scenario_normal(args.server, args.bed)
        else:
            scenario_multi(args.server)
    except urllib.error.URLError as e:
        print(f"[오류] 서버에 연결 실패: {e}. 서버가 실행 중인지 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
