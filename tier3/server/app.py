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
from schema import FallEvent, Severity, is_alert  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(HERE), "static")

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
        """이벤트 수신 처리: ID 부여 → 상태/이력 갱신 → broadcast."""
        async with self._lock:
            event.event_id = self.next_event_id()
            d = event.to_dict()
            self.beds[event.bed_id] = d
            self.history.append(d)
            if len(self.history) > self.history_limit:
                self.history = self.history[-self.history_limit:]
        await self.broadcast({"type": "event", "event": d})
        return d

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
    return {"status": "ok", "dashboards": len(hub.dashboards), "beds": len(hub.beds)}


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
