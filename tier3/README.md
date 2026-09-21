# tier3/ — 간호 스테이션 알림·모니터링 웹앱 (Tier 3)

Tier 2(비전)·Tier 1(하중)이 낙상/위험을 감지하면 간호 스테이션 브라우저
대시보드에 **실시간 경보**를 띄운다. 나아가 하중 데이터를 이용해 **환자 상태
모니터링 플랫폼**(체위 변경·무게 이상·센서 감시)으로 확장한다. 2-Tier
파이프라인의 알림 단계.

### 기능
- **낙상 경보** — 전체화면 빨간 경보 + 소리 + 침대/시각/사유/P(Fall)
- **다중 낙상 큐** — 동시 낙상 시 한 건씩 순서대로, 확인하면 다음 알림 (놓침 방지)
- **센서 오프라인 감지** — 무응답 = 감시 중단, 카드에 오프라인 배지 + 경보
- **체위 변경 알림(욕창 예방)** — 장시간 동일 자세(COG/무게 정지) 시 알림
- **무게 이상 감지** — 재실 중 급격한 무게 변화, 무게 추세 표시

```
[Tier 2 비전 / Tier 1 하중 / mock] --POST /ingest 또는 WS /ws/ingest-->
      [FastAPI 서버] --WS /ws/dashboard--> [브라우저 대시보드(들)]
```

## 구성

| 파일 | 역할 |
|---|---|
| `schema.py` | 이벤트 데이터 규격 `FallEvent` (Tier 2/Tier 1 필드 통합) + `Severity` |
| `server/app.py` | FastAPI 서버 — 이벤트 수신, broadcast, 확인(ack), 이력, **백그라운드 모니터링(오프라인/체위/무게)**, 정적 서빙 |
| `static/index.html` | 대시보드 — 침대 카드, 낙상 전체화면 경보(빨강+소리), **다중 낙상 큐**, 오프라인/체위/무게 배지, 무게 추세, 확인 버튼, 이벤트 로그 |
| `mock_tier2.py` | 하드웨어 없이 검증용 — fall/normal/multi/concurrent/offline/posture/weight 시나리오 |
| `requirements-tier3.txt` | 서버 의존성 (fastapi, uvicorn) |

## 실행

### 1) 서버 기동
```bash
pip install -r tier3/requirements-tier3.txt
cd tier3/server
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
브라우저에서 **http://localhost:8000** 접속 → 대시보드.

서버는 모니터링 임계값을 환경변수로 조정한다. **테스트 시 짧게** 두면 빨리
확인할 수 있다 (기본: 체위 2시간, 오프라인 15초):
```bash
YMAS_POSTURE_INTERVAL_S=8 YMAS_OFFLINE_TIMEOUT_S=6 \
  python -m uvicorn app:app --port 8000
```
| 환경변수 | 기본 | 의미 |
|---|---|---|
| `YMAS_OFFLINE_TIMEOUT_S` | 15 | 이 시간 무응답이면 센서 오프라인 |
| `YMAS_POSTURE_INTERVAL_S` | 7200(2h) | 이 시간 동일 자세면 체위 변경 알림 |
| `YMAS_STILL_COG_MM` | 40 | COG 가 이 이하로만 움직이면 '정지' |
| `YMAS_WEIGHT_ANOMALY_KG` | 15 | 이 이상 급변하면 무게 이상 |

### 2) 하드웨어 없이 테스트 (다른 터미널)
```bash
python tier3/mock_tier2.py --scenario fall        # 정상→주의→위험→낙상
python tier3/mock_tier2.py --scenario normal      # 낙상 경보 없어야 정상
python tier3/mock_tier2.py --scenario multi       # 여러 침대 혼합
python tier3/mock_tier2.py --scenario concurrent  # 동시 낙상 2건 → 큐 검증
python tier3/mock_tier2.py --scenario offline     # 신호 끊김 → 오프라인 감지
python tier3/mock_tier2.py --scenario posture     # 동일 자세 지속 → 체위 알림
python tier3/mock_tier2.py --scenario weight      # 무게 급변 → 이상 감지
```

## 이벤트 스키마 (`FallEvent`)

Tier 2 실물(`edge/ymas_realtime_ir.py`)과 Tier 1 펌웨어가 실제로 가진 값에
맞춰 정의했다. 필수는 `bed_id`, `severity`뿐이고 나머지는 있는 값만 채운다.

| 필드 | 출처 | 설명 |
|---|---|---|
| `bed_id`, `severity` | 공통 | 침대 ID / NORMAL·CAUTION·DANGER·FALL |
| `event_type` | 공통 | fall · posture_alert · weight_anomaly · sensor_offline · status |
| `reason`, `message` | Tier1/2/서버 | 트리거 사유 / 사람이 읽을 문구 |
| `p_normal/p_risk/p_fall` | Tier2 | ST-GCN 3클래스 확률 |
| `tilt_final`, `zone_status`, `exit_tag` | Tier2 | 몸통 기울기 / zone 이탈 / EXIT 태그 |
| `total_kg`, `cog_x`, `cog_y`, `edge_ratio` | Tier1 | 총중량 / 무게중심 / 이탈도 |
| `still_seconds`, `weight_delta` | 서버 | 체위 정지 시간 / 무게 변화량 |
| `event_id`, `acknowledged`, `ack_ts` | 서버 | 서버 부여 ID / 확인 여부·시각 |

`event_type` 이 없으면 `severity` 로 추론한다(하위 호환): FALL/DANGER→fall,
그 외→status. `sensor_offline`·`posture_alert`·`weight_anomaly` 는 서버가
백그라운드로 생성한다.

> **자세 분류(똑바로/좌/우 누움)** 는 Tier 2 새 학습 + 카메라 실데이터가
> 필요하므로 Phase 2(실데이터 수집) 이후로 보류. 현재 체위 알림은 Tier 1
> 하중/COG 정지 여부만으로 판정한다.

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

## 검증 완료 (로컬 E2E + 시각, 하드웨어 없음)

FastAPI TestClient 로 파이프라인 + 모니터링 검증:
- 대시보드 WS 연결 → 스냅샷 → 정상/낙상 ingest → 실시간 수신 → 확인(ack) broadcast
- **다중 낙상**: 두 침대 동시 낙상 → 큐로 대기 처리 (스크린샷상 "대기 중인 낙상 경보 1건")
- **센서 오프라인**: 무응답 시 sensor_offline 발생, 오프라인 중엔 체위/무게 판정 스킵
- **체위 변경**: 동일 자세 지속 시 posture_alert 발생
- **무게 이상**: 급변(+22kg) 감지, weight_delta 정확
- 여분 필드 무시(스키마 견고성)
- 시각 검증(스크린샷): 정상 4침대 카드, 다중 낙상 큐 오버레이 확인

> 참고: `schema.py`는 Python 3.9 호환(`Optional[...]`). 서버는 3.9+ 동작.
> 검증 중 발견·수정: (1) Python 3.9 비호환 `float|None`, (2) 오프라인 상태에서
> 체위 알림이 뜨는 모순 — 오프라인이면 체위/무게 판정을 건너뛰도록 수정.
