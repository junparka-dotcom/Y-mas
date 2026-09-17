"""NTU 25-joint 스켈레톤 정의 (관절 인덱스 / 뼈대 연결).

주의: 여기 정의된 관절 인덱스와 BONE 연결은 학습·전처리·모델(그래프
인접행렬) 전체가 공유하는 단일 진실 소스다. 이 값을 바꾸면
normalize_seq / physics_features / build_adjacency 가 모두 영향을 받으므로
절대 임의로 수정하지 말 것.

NTU_BONES 는 1-indexed (원본 NTU 규약). 코드에서 배열 인덱싱에 쓸 때는
(a-1, b-1) 로 변환한다.
"""

# NTU 25-joint 뼈대 연결 (1-indexed, 원본 노트북 CELL 2 와 동일)
NTU_BONES = [
    (1, 2), (2, 21), (3, 21), (4, 3), (5, 21), (6, 5), (7, 6), (8, 7),
    (9, 21), (10, 9), (11, 10), (12, 11), (13, 1), (14, 13), (15, 14),
    (16, 15), (17, 1), (18, 17), (19, 18), (20, 19), (21, 2),
    (22, 23), (23, 8), (24, 25), (25, 12),
]

# 주요 관절 인덱스 (0-indexed). 노트북 CELL 2 와 동일.
J_SPINE_BASE = 0
J_SPINE_MID = 1
J_NECK = 2
J_HEAD = 3
J_SHL = 4          # 왼쪽 어깨
J_SHR = 8          # 오른쪽 어깨
J_SPINE_SHOULDER = 20   # 그래프 중심 관절

CLASS_NAMES = ["Normal", "Risk", "Fall"]

# physics 특징 이름 (순서 고정 — pmean/pstd 와 1:1 대응)
PHYS_NAMES = [
    "vh_max", "vs_max", "head_drop", "head_final",
    "tilt_max", "tilt_final", "horiz_max", "motion_max",
    "spine_span", "shoulder_asym",
]
