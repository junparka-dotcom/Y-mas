# tier3/ — 간호 스테이션 낙상 알림 웹앱 (Tier 3)

Tier 2(비전) 또는 Tier 1(하중)이 낙상/위험을 감지하면, 간호 스테이션
브라우저 대시보드에 **실시간으로 경보**를 띄운다. 2-Tier 감지 파이프라인의
마지막 단계(알림)를 담당한다.

```
[Tier 2 비전 / Tier 1 하중 / mock] --POST /ingest 또는 WS /ws/ingest-->
      [FastAPI 서버] --WS /ws/dashboard--> [브라우저 대시보드(들)]
```

## 구성

| 파일 | 역할 |
|---|---|
| `schema.py` | 이벤트 데이터 규격 `FallEvent` (Tier 2/Tier 1 필드 통합) + `Severity` |
| `server/app.py` | FastAPI 서버 — 이벤트 수신, 대시보드 broadcast, 확인(ack), 이력, 정적 서빙 |
| `static/index.html` | 대시보드 — 침대별 상태 카드, 낙상 시 전체화면 경보(빨강+소리), 확인 버튼, 이벤트 로그 |
| `mock_tier2.py` | 하드웨어 없이 검증용 — 낙상/정상 시나리오를 서버로 전송 |
| `requirements-tier3.txt` | 서버 의존성 (fastapi, uvicorn) |

## 실행

### 1) 서버 기동
```bash
pip install -r tier3/requirements-tier3.txt
cd tier3/server
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
브라우저에서 **http://localhost:8000** 접속 → 대시보드.

### 2) 하드웨어 없이 낙상 알림 테스트 (다른 터미널)
```bash
# 낙상 시나리오: 정상 → 주의 → 위험 → 낙상 (대시보드에 빨간 경보가 떠야 함)
python tier3/mock_tier2.py --scenario fall --bed 301-A

# 정상 시나리오: 낙상 경보가 뜨지 않아야 정상
python tier3/mock_tier2.py --scenario normal --bed 301-A

# 여러 침대 혼합
python tier3/mock_tier2.py --scenario multi
```

## 이벤트 스키마 (`FallEvent`)

Tier 2 실물(`edge/ymas_realtime_ir.py`)과 Tier 1 펌웨어가 실제로 가진 값에
맞춰 정의했다. 필수는 `bed_id`, `severity`뿐이고 나머지는 있는 값만 채운다.

| 필드 | 출처 | 설명 |
|---|---|---|
| `bed_id`, `severity` | 공통 | 침대 ID / NORMAL·CAUTION·DANGER·FALL |
| `reason` | Tier1/2 | 트리거 사유 (FASTPATH_WEIGHT_DROP 등) |
| `p_normal/p_risk/p_fall` | Tier2 | ST-GCN 3클래스 확률 |
| `tilt_final`, `zone_status`, `exit_tag` | Tier2 | 몸통 기울기 / zone 이탈 / EXIT 태그 |
| `total_kg`, `cog_x`, `cog_y`, `edge_ratio` | Tier1 | 총중량 / 무게중심 / 이탈도 |
| `event_id`, `acknowledged`, `ack_ts` | 서버 | 서버 부여 ID / 확인 여부·시각 |

## 실제 Tier 2 연결 (나중에)

Tier 2 실물은 낙상 확정 시 `FallEvent` 스키마로 서버 `/ingest`에 POST 하거나
`/ws/ingest`로 push 하면 된다. `edge/ymas_realtime_ir.py`의 낙상 판정
(`pred_class == 2`) 지점에서 아래처럼 호출:

```python
import urllib.request, json
ev = {"bed_id": "301-A", "severity": "FALL", "source": "tier2",
      "reason": "vision", "p_fall": float(probs[2]), "tilt_final": float(phys[5])}
urllib.request.urlopen(urllib.request.Request(
    "http://<서버IP>:8000/ingest",
    data=json.dumps(ev).encode(), headers={"Content-Type":"application/json"}))
```

## 검증 완료 (로컬 E2E, 하드웨어 없음)

FastAPI TestClient 로 전체 파이프라인 검증:
- 대시보드 WS 연결 → 스냅샷 수신
- 정상/낙상 이벤트 ingest → 대시보드 실시간 수신 (P(Fall), 사유 포함)
- 간호사 확인(ack) → 전 대시보드 broadcast
- 이력/침대 상태 API, 여분 필드 무시(스키마 견고성)
- mock fall 시나리오: NORMAL→CAUTION→DANGER→FALL 전이 확인

> 참고: `schema.py`는 Python 3.9 호환(`Optional[...]` 사용). 서버는 3.9+ 동작.
