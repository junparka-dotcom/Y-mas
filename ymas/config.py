"""전역 설정값 + NTU/ETRI 피험자 분할.

노트북 CELL 2 와 동일. 하이퍼파라미터는 v13 그리드서치 확정값이며,
피험자 분할은 v13 이후 고정되어 재현성을 위해 절대 바꾸지 않는다.

분할 원칙:
  - Val/Holdout 은 A043(Fall) 이 확실히 있는 NTU-60 피험자(1-40) 에서만.
  - ETRI 는 고령(P001-P050)/청년(P051-P100) 이 각 스플릿에 고루 들어가도록
    10 간격 추출.
  - subject ID 충돌 방지: ETRI subject 에 +1000 오프셋 (NTU 1-106, ETRI 1-100).
"""

from dataclasses import dataclass, field, asdict


# ---------------- NTU 라벨 정의 ----------------
FALL_ACTIONS = {43}
RISK_ACTIONS = {6, 8, 9, 26, 27, 42, 48}
SINGLE_PERSON_ACTIONS = set(range(1, 50)) | set(range(61, 107))

# ---------------- ETRI 라벨 정의 ----------------
ETRI_FALL_ACTIONS = {53}                 # fallen on the floor
ETRI_NORMAL_ACTIONS = {
    11, 12,        # 손씻기 / 세수        - 몸을 숙이는 동작
    20,            # 신발 신고벗기        - 앉아 숙이는 동작
    23, 24,        # 청소기 / 걸레질      - 낮은 자세 유지
    27,            # 이불 정리            - 침상 맥락 직결
    41, 42, 43,    # 맨손체조/목운동/어깨마사지
    48,            # 싸움                 - 급격한 움직임 (오탐 검증)
    54,            # 앉기 / 서기
    55,            # 눕기                 - Fall 과 대조 학습
}
ETRI_ACTION_NAMES = {
    11: 'washing hands', 12: 'washing face', 20: 'putting on/off shoes',
    23: 'vacuuming', 24: 'scrubbing floor', 27: 'spreading/folding bedding',
    41: 'freehand exercise', 42: 'neck roll exercise', 43: 'shoulder massage',
    48: 'fighting', 53: 'FALLEN ON THE FLOOR', 54: 'sitting up/standing up',
    55: 'lying down',
}

# ---------------- NTU 피험자 분할 (v13 과 동일) ----------------
_ntu60_train = {1, 2, 4, 5, 8, 9, 13, 14, 15, 16, 17, 18, 19, 25, 27, 28, 31, 34, 35, 38}
_ntu60_pool = sorted(set(range(1, 41)) - _ntu60_train)

TRAIN_SUBJECTS = _ntu60_train | set(range(41, 107))   # 86명
VAL_SUBJECTS = set(_ntu60_pool[:10])                  # 10명
HOLDOUT_SUBJECTS = set(_ntu60_pool[10:])              # 10명
assert len(TRAIN_SUBJECTS) == 86
assert len(VAL_SUBJECTS) == 10
assert len(HOLDOUT_SUBJECTS) == 10

# ---------------- ETRI 피험자 분할 ----------------
ETRI_VAL_SUBJECTS = {3, 13, 23, 33, 43, 53, 63, 73, 83, 93}
ETRI_HOLDOUT_SUBJECTS = {7, 17, 27, 37, 47, 57, 67, 77, 87, 97}
ETRI_TRAIN_SUBJECTS = set(range(1, 101)) - ETRI_VAL_SUBJECTS - ETRI_HOLDOUT_SUBJECTS
assert len(ETRI_TRAIN_SUBJECTS) == 80

ETRI_SUBJ_OFFSET = 1000   # subject ID 충돌 방지


@dataclass
class Config:
    """학습/전처리 설정 (v13 그리드서치 확정 하이퍼파라미터)."""
    # 데이터
    T: int = 64
    V: int = 25
    fps: float = 30.0
    n_phys: int = 10
    # 캐시 상한 (원본 파싱 시에만 사용; 캐시 로드 시 무관)
    normal_cap: int = 120
    etri_norm_cap: int = 150
    etri_fall_cap: int = 1200
    # 학습
    batch_size: int = 64
    epochs: int = 40
    lr: float = 1e-3
    weight_decay: float = 5e-4
    warmup_epochs: int = 3
    label_smooth: float = 0.15      # v13 확정
    focal_gamma: float = 1.5        # v13 확정
    dropout: float = 0.45           # v13 확정
    aux_weight: float = 0.15        # v13 확정
    mixup_alpha: float = 0.4
    ema_decay: float = 0.9999
    swa_start_r: float = 0.75
    seed: int = 42

    def to_dict(self):
        return asdict(self)


def subject_split_ntu(subj):
    if subj in TRAIN_SUBJECTS:
        return 'train'
    if subj in VAL_SUBJECTS:
        return 'val'
    if subj in HOLDOUT_SUBJECTS:
        return 'holdout'
    return None


def subject_split_etri(p):
    if p in ETRI_TRAIN_SUBJECTS:
        return 'train'
    if p in ETRI_VAL_SUBJECTS:
        return 'val'
    if p in ETRI_HOLDOUT_SUBJECTS:
        return 'holdout'
    return None


def action_to_label(a):
    """NTU action id -> 라벨 (0=Normal, 1=Risk, 2=Fall, None=제외)."""
    if a in FALL_ACTIONS:
        return 2
    if a in RISK_ACTIONS:
        return 1
    if a in SINGLE_PERSON_ACTIONS:
        return 0
    return None


def etri_action_to_label(a):
    if a in ETRI_FALL_ACTIONS:
        return 2
    if a in ETRI_NORMAL_ACTIONS:
        return 0
    return None
