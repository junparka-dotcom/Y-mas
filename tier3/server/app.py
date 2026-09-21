"""Tier 3 간호 스테이션 알림 서버 (FastAPI + WebSocket).

파이프라인:
    [Tier 2 / Tier 1 / mock] --POST /ingest 또는 WS /ws/ingest-->
        [이 서버] --WS /ws/dashboard--> [브라우저 대시보드(들)]

역할:
    1) Tier 2/1 이벤트 수신 (HTTP POST 또는 WebSocket)
    2) 이벤트에 서버 ID 부여 + 이력 저장 + 침대별 최신 상태 갱신
    3) 연결된 모든 대시보드에 실시간 broadcast
    4) 간호사 '확인(acknowledge)' 처리
    5) 정적 대시보드(static/) 서빙

의존성: fastapi, uvicorn  (requirements-tier3.txt)
실행:   uvicorn tier3.server.app:app --host 0.0.0.0 --port 8000
        또는  python -m tier3.server.app
"""

import asyncio
import itertools
import os
import time
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schema import FallEvent, Severity, EventType, is_alert  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(HERE), "static")

# ---------------- 모니터링 설정 (환경변수로 조정 가능) ----------------
# 테스트 시 짧게: 예) YMAS_POSTURE_INTERVAL_S=30 YMAS_OFFLINE_TIMEOUT_S=10
MONITOR = dict(
    # 센서 오프라인: 마지막 수신 후 이 시간(s) 넘게 무응답이면 경고
    offline_timeout_s=float(os.environ.get("YMAS_OFFLINE_TIMEOUT_S", "15")),
    # 체위 변경(욕창): COG/무게가 이 거리·무게 안에서만 움직이면 '정지'로 간주
    still_cog_mm=float(os.environ.get("YMAS_STILL_COG_MM", "40")),
    still_weight_kg=float(os.environ.get("YMAS_STILL_WEIGHT_KG", "3")),
    # 이 시간(s) 넘게 정지 지속이면 체위 변경 알림 (기본 2시간=7200s)
    posture_interval_s=float(os.environ.get("YMAS_POSTURE_INTERVAL_S", "7200")),
    # 무게 이상: 짧은 시간에 이 이상(kg) 급변(낙상 아닌 재실 상태에서)이면 경고
    weight_anomaly_kg=float(os.environ.get("YMAS_WEIGHT_ANOMALY_KG", "15")),
    # 백그라운드 점검 주기(s)
    check_period_s=float(os.environ.get("YMAS_CHECK_PERIOD_S", "2")),
)

app = FastAPI(title="Y-mas Tier 3 — 간호 스테이션 알림")


class Hub:
    """대시보드 연결 관리 + 상태/이력 보관 + broadcast."""

    def __init__(self, history_limit=500):
        self.dashboards: List[WebSocket] = []
        self.beds: Dict[str, dict] = {}        # bed_id -> 최신 이벤트 dict
        self.history: List[dict] = []          # 전체 이벤트 이력
        self.history_limit = history_limit
        self._id_gen = itertools.count(1)
        self._lock = asyncio.Lock()
        # 모니터링 상태 (bed_id -> 추적 정보)
        self.monitor: Dict[str, dict] = {}
        # 이미 한 번 알린 상태(중복 알림 방지): bed_id -> set(event_type)
        self.raised: Dict[str, set] = {}

    def next_event_id(self) -> str:
        return f"evt-{int(time.time())}-{next(self._id_gen)}"

    async def register(self, ws: WebSocket):
        await ws.accept()
        self.dashboards.append(ws)
        # 신규 대시보드에 현재 스냅샷 전송
        await ws.send_json({
            "type": "snapshot",
            "beds": list(self.beds.values()),
            "history": self.history[-50:],
        })

    def unregister(self, ws: WebSocket):
        if ws in self.dashboards:
            self.dashboards.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.dashboards:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    async def ingest(self, event: FallEvent) -> dict:
        """이벤트 수신 처리: ID 부여 → 상태/이력 갱신 → 모니터링 추적 → broadcast."""
        async with self._lock:
            event.event_id = self.next_event_id()
            d = event.to_dict()
            self.beds[event.bed_id] = d
            self.history.append(d)
            if len(self.history) > self.history_limit:
                self.history = self.history[-self.history_limit:]
            self._track(d)
        await self.broadcast({"type": "event", "event": d})
        return d

    def _track(self, d: dict):
        """모니터링 상태 갱신 (센서 heartbeat / 체위 정지 / 무게 기준).

        Tier 1/2 가 보낸 실측 데이터(total_kg, cog_x/y)가 있을 때만 추적.
        """
        bed = d["bed_id"]
        now = time.time()
        m = self.monitor.get(bed)
        if m is None:
            m = dict(last_seen=now, last_move=now,
                     ref_cog=None, ref_weight=None, last_weight=None)
            self.monitor[bed] = m
            self.raised[bed] = set()
        m["last_seen"] = now
        # 센서가 다시 응답 -> offline 경고 상태 해제
        self.raised[bed].discard(EventType.SENSOR_OFFLINE.value)

        cx, cy = d.get("cog_x"), d.get("cog_y")
        w = d.get("total_kg")

        # 체위(정지) 추적: COG 가 기준점에서 still_cog_mm 이상 움직이면 '움직임'
        if cx is not None and cy is not None:
            ref = m["ref_cog"]
            moved = (ref is None or
                     abs(cx - ref[0]) > MONITOR["still_cog_mm"] or
                     abs(cy - ref[1]) > MONITOR["still_cog_mm"])
            if w is not None and m["ref_weight"] is not None:
                moved = moved or abs(w - m["ref_weight"]) > MONITOR["still_weight_kg"]
            if moved:
                m["ref_cog"] = (cx, cy)
                m["ref_weight"] = w
                m["last_move"] = now
                self.raised[bed].discard(EventType.POSTURE_ALERT.value)

        # 무게 이상 급변 추적 (직전 수신 대비)
        if w is not None:
            prev = m["last_weight"]
            m["_weight_jump"] = (w - prev) if prev is not None else 0.0
            m["last_weight"] = w

        # 침대가 비면(무게 낮음) 모니터링 리셋
        if w is not None and w < 15:
            m["ref_cog"] = None
            m["ref_weight"] = None
            m["last_move"] = now
            self.raised[bed].clear()

    async def check_monitors(self):
        """주기적 점검 → 센서 오프라인 / 체위 변경 / 무게 이상 이벤트 생성."""
        now = time.time()
        to_raise = []
        async with self._lock:
            for bed, m in self.monitor.items():
                raised = self.raised.setdefault(bed, set())
                # 1) 센서 오프라인
                offline = now - m["last_seen"] > MONITOR["offline_timeout_s"]
                if offline and EventType.SENSOR_OFFLINE.value not in raised:
                    raised.add(EventType.SENSOR_OFFLINE.value)
                    to_raise.append(FallEvent(
                        bed_id=bed, severity=Severity.DANGER.value,
                        event_type=EventType.SENSOR_OFFLINE.value, source="server",
                        reason="no_signal",
                        message=f"센서 무응답 {int(now - m['last_seen'])}초 — 감시 중단 위험"))
                # 센서가 오프라인이면 신호가 없으므로 체위/무게 판정은 무의미 -> 건너뜀
                if offline:
                    continue
                # 2) 체위 변경 필요 (욕창) — 침대에 사람이 있을 때만
                still = now - m["last_move"]
                bed_state = self.beds.get(bed, {})
                occupied = (bed_state.get("total_kg") or 0) >= 15
                if (occupied and still > MONITOR["posture_interval_s"]
                        and EventType.POSTURE_ALERT.value not in raised):
                    raised.add(EventType.POSTURE_ALERT.value)
                    to_raise.append(FallEvent(
                        bed_id=bed, severity=Severity.CAUTION.value,
                        event_type=EventType.POSTURE_ALERT.value, source="server",
                        reason="stillness", still_seconds=round(still, 1),
                        message=f"체위 변경 필요 — {int(still//60)}분 이상 동일 자세 (욕창 예방)"))
                # 3) 이상 무게 급변 (마지막 수신에서 감지된 점프)
                jump = m.get("_weight_jump", 0.0)
                if (occupied and abs(jump) >= MONITOR["weight_anomaly_kg"]
                        and EventType.WEIGHT_ANOMALY.value not in raised):
                    raised.add(EventType.WEIGHT_ANOMALY.value)
                    to_raise.append(FallEvent(
                        bed_id=bed, severity=Severity.CAUTION.value,
                        event_type=EventType.WEIGHT_ANOMALY.value, source="server",
                        reason="weight_jump", weight_delta=round(jump, 1),
                        message=f"이상 무게 변화 {jump:+.1f}kg 감지"))
                elif abs(jump) < MONITOR["weight_anomaly_kg"]:
                    raised.discard(EventType.WEIGHT_ANOMALY.value)
        for ev in to_raise:
            await self.ingest(ev)

    async def acknowledge(self, event_id: str) -> bool:
        """간호사 확인 처리. 이력·침대 상태 양쪽에서 표시."""
        found = False
        ack_ts = time.time()
        async with self._lock:
            for d in reversed(self.history):
                if d.get("event_id") == event_id:
                    d["acknowledged"] = True
                    d["ack_ts"] = ack_ts
                    bed = self.beds.get(d["bed_id"])
                    if bed and bed.get("event_id") == event_id:
                        bed["acknowledged"] = True
                        bed["ack_ts"] = ack_ts
                    found = True
                    break
        if found:
            await self.broadcast({"type": "ack", "event_id": event_id, "ack_ts": ack_ts})
        return found


hub = Hub()


# ---------------- Tier 2/1 → 서버 : 이벤트 수신 ----------------
@app.post("/ingest")
async def ingest_http(payload: dict):
    """HTTP POST 로 이벤트 수신 (mock/Tier2 겸용, 간단한 통합에 적합)."""
    event = FallEvent.from_dict(payload)
    d = await hub.ingest(event)
    return JSONResponse({"ok": True, "event_id": d["event_id"]})


@app.websocket("/ws/ingest")
async def ingest_ws(ws: WebSocket):
    """WebSocket 으로 이벤트 스트림 수신 (고빈도 상태 push 에 적합)."""
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            event = FallEvent.from_dict(payload)
            await hub.ingest(event)
    except WebSocketDisconnect:
        pass


# ---------------- 서버 → 대시보드 : 실시간 push ----------------
@app.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket):
    await hub.register(ws)
    try:
        while True:
            # 대시보드에서 오는 메시지 (확인 요청 등)
            msg = await ws.receive_json()
            if msg.get("type") == "ack":
                await hub.acknowledge(msg.get("event_id", ""))
    except WebSocketDisconnect:
        hub.unregister(ws)


# ---------------- REST 보조 API ----------------
@app.get("/api/beds")
async def api_beds():
    return {"beds": list(hub.beds.values())}


@app.get("/api/history")
async def api_history(limit: int = 50):
    return {"history": hub.history[-limit:]}


@app.post("/api/ack/{event_id}")
async def api_ack(event_id: str):
    ok = await hub.acknowledge(event_id)
    return {"ok": ok}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "dashboards": len(hub.dashboards),
            "beds": len(hub.beds), "monitor": MONITOR}


# ---------------- 백그라운드 모니터링 루프 ----------------
@app.on_event("startup")
async def _start_monitor():
    async def loop():
        while True:
            try:
                await hub.check_monitors()
            except Exception as e:  # 루프가 죽지 않도록
                print("monitor loop error:", e)
            await asyncio.sleep(MONITOR["check_period_s"])
    app.state.monitor_task = asyncio.create_task(loop())


@app.on_event("shutdown")
async def _stop_monitor():
    t = getattr(app.state, "monitor_task", None)
    if t:
        t.cancel()


# ---------------- 정적 대시보드 ----------------
@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(STATIC_DIR, "index.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
