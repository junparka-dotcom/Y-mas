"""Y-mas Tier 2 vision AI package.

병실 침대 낙상 감지 시스템의 스켈레톤 기반 행동 분류 모델.
NTU RGB+D 120 + ETRI-Activity3D (둘 다 Kinect V2 계열) 로 학습.

이 패키지는 검증된 v17 Colab 노트북을 모듈로 분해한 것이다.
전처리 불변식(preprocess.py)은 노트북과 바이트 단위로 동일해야 하며,
학습 코드와 추론 코드에서 완전히 같은 함수를 써야 한다.
"""

__version__ = "17.0.0"
