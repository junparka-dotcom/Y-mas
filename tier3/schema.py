"""Tier 3 이벤트 데이터 스키마 — Tier 2(비전) / Tier 1(하중)이 보낼 페이로드 규격.

이 스키마는 Tier 2 실물(edge/ymas_realtime_ir.py, integration/ymas_integrated.py)과
Tier 1 펌웨어 출력에서 실제로 얻을 수 있는 값에 맞춰 정의한다 (넘겨짚지 않음).

Tier 2 가 가진 정보 (ymas_realtime_ir.py 실측):
    - 클래스: Normal/Risk/Fall, 3클래스 확률 p_normal/p_risk/p_fall
    - tilt_final(몸통 기울기), zone 상태, exit 태그, 시각
Tier 1 이 가진 정보 (펌웨어 emit):
    - total(총중량), cog_x/cog_y, edge_ratio, reason(FASTPATH/PREDICTIVE/STANDARD)

두 계층 정보를 합쳐 하나의 이벤트로 표현한다. 필드는 대부분 선택적이며,
어느 계층이 보냈든 있는 값만 채운다.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import time


class Severity(str, Enum):
    """대시보드 표시 심각도 (Tier 1 상태 + Tier 2 판정 통합)."""
    NORMAL = "NORMAL"      # 정상
    CAUTION = "CAUTION"    # 주의 (가장자리 접근 등)
    DANGER = "DANGER"      # 위험 (임계 근접)
    FALL = "FALL"          # 낙상 확정 (Tier 2) — 최우선 경고


class EventType(str, Enum):
    """이벤트 종류 — 낙상 감지를 넘어 '환자 상태 모니터링 플랫폼' 으로 확장.

    fall           : 낙상/위험 (기존). Tier 1/2 감지.
    posture_alert  : 체위 변경 필요 (욕창 예방). 장시간 COG/무게 정지 시 서버 생성.
    weight_anomaly : 이상 무게 변화 (낙상 아닌 급변). 서버 생성.
    sensor_offline : 센서 무응답 (감시 중단 = 안전 위험). 서버 생성.
    status         : 일반 상태 업데이트 (알림 아님).
    """
    STATUS = "status"
    FALL = "fall"
    POSTURE_ALERT = "posture_alert"
    WEIGHT_ANOMALY = "weight_anomaly"
    SENSOR_OFFLINE = "sensor_offline"


# Tier 1 펌웨어 상태 문자열 -> Severity 매핑
TIER1_STATE_TO_SEVERITY = {
    "EMPTY": Severity.NORMAL,
    "NORMAL": Severity.NORMAL,
    "CAUTION": Severity.CAUTION,
    "DANGER": Severity.DANGER,
    "ALERT": Severity.DANGER,   # Tier 1 ALERT 는 '위험 트리거'; 낙상 '확정'은 Tier 2 몫
}


@dataclass
class FallEvent:
    """Tier 2/Tier 1 → Tier 3 로 전송되는 단일 이벤트."""
    bed_id: str                          # 침대 식별자 (예: "301-A")
    severity: str                        # Severity 값
    event_type: str = "status"           # EventType 값 (기본 status)
    source: str = "tier2"                # "tier1" | "tier2" | "integrated" | "server"
    ts: float = field(default_factory=time.time)   # epoch seconds
    reason: str = ""                     # 트리거 사유 (FASTPATH_WEIGHT_DROP 등)
    message: str = ""                    # 사람이 읽을 알림 문구 (체위변경/오프라인 등)

    # --- Tier 2 (비전) 필드 ---
    p_normal: Optional[float] = None
    p_risk: Optional[float] = None
    p_fall: Optional[float] = None
    tilt_final: Optional[float] = None   # 최종 몸통 기울기(도)
    zone_status: Optional[str] = None    # zone 내/이탈
    exit_tag: Optional[str] = None       # "[EXIT+TILT]" 등

    # --- Tier 1 (하중) 필드 ---
    total_kg: Optional[float] = None
    cog_x: Optional[float] = None
    cog_y: Optional[float] = None
    edge_ratio: Optional[float] = None

    # --- 모니터링(플랫폼 확장) 필드 ---
    still_seconds: Optional[float] = None   # 체위 정지 지속 시간(s) — 욕창 타이머
    weight_delta: Optional[float] = None    # 무게 변화량(kg) — 이상 감지

    # --- Tier 3 관리 필드 (서버가 채움) ---
    event_id: Optional[str] = None       # 서버 부여 고유 ID
    acknowledged: bool = False           # 간호사 확인 여부
    ack_ts: Optional[float] = None       # 확인 시각

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "FallEvent":
        """수신 payload(dict) -> FallEvent. 알 수 없는 키는 무시.

        event_type 가 없으면 severity 로부터 추론(하위 호환):
        FALL/DANGER -> fall, 그 외 -> status.
        """
        fields = FallEvent.__dataclass_fields__
        kept = {k: v for k, v in d.items() if k in fields}
        kept.setdefault("bed_id", "unknown")
        kept.setdefault("severity", Severity.NORMAL.value)
        if "event_type" not in kept:
            sev = kept["severity"]
            kept["event_type"] = (EventType.FALL.value
                                  if sev in (Severity.FALL.value, Severity.DANGER.value)
                                  else EventType.STATUS.value)
        return FallEvent(**kept)


# 대시보드에서 '경보(알림 큐/배지)'로 처리할 이벤트 종류
ALERT_EVENT_TYPES = {
    EventType.FALL.value,
    EventType.POSTURE_ALERT.value,
    EventType.WEIGHT_ANOMALY.value,
    EventType.SENSOR_OFFLINE.value,
}


def is_alert(severity: str) -> bool:
    """대시보드에서 '경보'로 크게 띄워야 하는 심각도인지 (낙상 계열)."""
    return severity in (Severity.FALL.value, Severity.DANGER.value)


def is_alert_event(event_type: str) -> bool:
    """알림으로 처리할 이벤트 종류인지."""
    return event_type in ALERT_EVENT_TYPES
