"""전처리 불변식 함수.

[!] 매우 중요 (인수인계 교훈 5)
    normalize_seq, physics_features, posture_features, resample, to_streams,
    temporal_crop 는 학습 코드와 추론 코드에서 **완전히 동일** 해야 한다.
    이게 어긋나면 모델이 조용히 틀린다. 이 파일의 함수는 검증된 v17
    Colab 노트북(CELL 4, CELL 7)과 바이트 단위로 동일하게 유지한다.

    - normalize_seq   : SpineBase 원점 이동 + 어깨선 기준 Y축 회전 + 몸통 길이 정규화
    - physics_features: 10개 물리 특징 (pmean/pstd 와 순서 일치)
    - resample        : 시퀀스를 고정 길이 T 로 리샘플 (선형 보간)
    - to_streams      : joint/bone/velocity 3-스트림 (C=9) 생성, (C,T,V) 전치
    - temporal_crop   : 학습 augmentation 전용 시간축 랜덤 크롭
"""

import numpy as np

from .skeleton import (
    NTU_BONES,
    J_SPINE_BASE, J_HEAD, J_SHL, J_SHR, J_SPINE_SHOULDER,
)


def normalize_seq(seq):
    """SpineBase 원점 이동 + 어깨선 기준 Y축 회전 + 몸통 길이 정규화.

    Returns:
        (normalized_seq: (T,25,3) float32, torso: float)
    """
    seq = seq.copy()
    origin = seq[0, J_SPINE_BASE].copy()
    seq -= origin
    sh = seq[0, J_SHR] - seq[0, J_SHL]
    theta = np.arctan2(sh[2], sh[0])
    c, s = np.cos(-theta), np.sin(-theta)
    R = np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]], dtype=np.float32)
    seq = seq @ R.T
    torso = np.linalg.norm(seq[0, J_SPINE_SHOULDER] - seq[0, J_SPINE_BASE])
    if torso < 1e-3:
        torso = 1.0
    return (seq / torso).astype(np.float32), float(torso)


def posture_features(seq):
    """v14 신규: 최종 자세의 '형태' 2개 특징.

    속도가 아닌 정적 자세 기반이라 기존 8개와 상호보완.

    spine_span    마지막 5프레임 전신 Y축 확장도
                  누움(팔다리 뻗음)=큼 / 쓰러짐(웅크림)=작음
    shoulder_asym 마지막 5프레임 좌우 어깨 Y 차이
                  정돈된 자세=작음 / 흐트러진 낙상=큼
    """
    tail = seq[-5:] if len(seq) >= 5 else seq
    all_y = tail[:, :, 1]
    spine_span = float(all_y.max() - all_y.min())
    shoulder_asym = float(np.abs(tail[:, J_SHR, 1] - tail[:, J_SHL, 1]).mean())
    return spine_span, shoulder_asym


def physics_features(seq, fps):
    """v14/v15: 10개 물리 특징.

      [0] vh.max()          Head 최대 하강속도
      [1] vs.max()          SpineBase 최대 하강속도
      [2] hy[0]-hy.min()    Head 최대 낙하량
      [3] hy[-5:].mean()    최종 Head 높이
      [4] tilt.max()        몸통 최대 기울기(도)
      [5] tilt[-5:].mean()  최종 몸통 기울기
      [6] horiz.max()       SpineBase 수평이동 최대
      [7] motion.max()      전관절 평균 모션 최대
      [8] spine_span        (신규) 최종 자세 전신 확장도
      [9] shoulder_asym     (신규) 최종 자세 좌우 비대칭
    """
    dt = 1.0 / fps
    hy = seq[:, J_HEAD, 1]
    sy = seq[:, J_SPINE_BASE, 1]
    vh = -np.diff(hy) / dt
    vs = -np.diff(sy) / dt
    body = seq[:, J_HEAD] - seq[:, J_SPINE_BASE]
    n = np.linalg.norm(body, axis=1) + 1e-6
    tilt = np.degrees(np.arccos(np.clip(body[:, 1] / n, -1.0, 1.0)))
    horiz = np.linalg.norm(seq[:, J_SPINE_BASE, [0, 2]], axis=1)
    motion = np.linalg.norm(np.diff(seq, axis=0), axis=2).mean(axis=1) / dt
    span, asym = posture_features(seq)
    return np.array([vh.max(), vs.max(), hy[0] - hy.min(), hy[-5:].mean(),
                     tilt.max(), tilt[-5:].mean(), horiz.max(), motion.max(),
                     span, asym],
                    dtype=np.float32)


def resample(seq, T):
    """시퀀스를 고정 길이 T 로 리샘플 (선형 보간)."""
    idx = np.linspace(0, len(seq) - 1, T)
    lo = np.floor(idx).astype(int)
    hi = np.ceil(idx).astype(int)
    w = (idx - lo)[:, None, None].astype(np.float32)
    return (seq[lo] * (1 - w) + seq[hi] * w).astype(np.float32)


# ---- bone pairs (0-indexed) : to_streams 에서 사용 ----
_BONE_PAIRS = [(a - 1, b - 1) for a, b in NTU_BONES]


def to_streams(x):
    """joint / bone / velocity 3-스트림 생성 후 (C=9, T, V) 로 전치.

    입력 x: (T, V, 3)
    출력  : (9, T, V)  -- joint(3) + bone(3) + velocity(3)
    """
    joint = x.copy()
    bone = np.zeros_like(x)
    for a, b in _BONE_PAIRS:
        bone[:, a] = x[:, a] - x[:, b]
    vel = np.zeros_like(x)
    vel[1:] = x[1:] - x[:-1]
    return np.concatenate([joint, bone, vel], axis=2).transpose(2, 0, 1)


def temporal_crop(x, T_out):
    """학습 augmentation 전용: 시간축 랜덤 크롭 후 T_out 으로 리샘플."""
    T_in = len(x)
    ratio = np.random.uniform(0.6, 1.0)
    T_crop = max(int(T_in * ratio), 12)
    start = np.random.randint(0, T_in - T_crop + 1)
    crop = x[start:start + T_crop]
    idx = np.linspace(0, len(crop) - 1, T_out)
    lo = np.floor(idx).astype(int)
    hi = np.ceil(idx).astype(int)
    w = (idx - lo)[:, None, None].astype(np.float32)
    return (crop[lo] * (1 - w) + crop[hi] * w).astype(np.float32)
