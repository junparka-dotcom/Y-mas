# Y-mas — 병실 침대 낙상 감지 / 환자 상태 모니터링 플랫폼

낙상을 "감지"하는 것을 넘어 **낙상 전 위험을 예측(pre-fall)** 하고, 같은
센서 인프라 위에 욕창 예방 자세 분류 등 임상 모니터링을 얹는 **풀 베드
솔루션**을 지향하는 프로젝트입니다.

이 저장소는 검증된 v17 Colab 노트북을 **버전 관리 가능한 모듈 구조**로
재구성한 것입니다. 노트북이 곧 코드였던 기존 방식에서 벗어나, 이 래포가
코드의 단일 진실 소스이고 Colab/GPU 환경은 이 래포를 clone 해 학습만
돌리는 실행기 역할을 합니다.

---

## 시스템 개요 (2-Tier)

| 단계 | 역할 | 구성 |
|---|---|---|
| **Tier 1** | 상시 감시 · 무게중심(COG) 편향 감지 | 로드셀 4채널 → HX711 → ESP32, 80Hz |
| **Tier 2** | 정밀 판정 · 낙상 확정 | ToF 카메라 → YOLO11n-pose → depth 3D 리프팅 → **ST-GCN 기반 모델(이 래포)** |
| **Tier 3** | 알림 | 간호 스테이션 앱 (미구현) |

저비용·저연산 센서(Tier 1)가 24시간 감시하다 위험 신호가 잡히면 고연산
비전 AI(Tier 2)가 깨어나 판정하는 구조입니다. 이 래포는 **Tier 2 모델**을
다룹니다.

## 모델 (v17)

- ST-GCN + Adaptive Graph Adjacency + Multi-Scale TCN(커널 3/5/9)
  + Temporal Attention Pooling + Physics Features 10종 + Auxiliary Fall Head
- trainable 약 1.48M 파라미터 (state_dict 총합 1.50M, BN 버퍼 포함)
- 학습: Focal Loss(γ=1.5) + EMA + Mixup + SWA, 도메인 균형 체크포인트 선택
- 데이터: **NTU RGB+D 120 + ETRI-Activity3D** (둘 다 Kinect V2 계열)
- 배포: ONNX opset 18 → TensorRT FP16 (Jetson Orin Nano Super)

## 저장소 구조

```
ymas/
  skeleton.py    NTU 25관절 정의 / 뼈대 연결 / 클래스·특징 이름 (단일 진실 소스)
  preprocess.py  전처리 불변식 (normalize_seq, physics_features, to_streams ...)
  config.py      Config + 피험자 분할 + 라벨 정의
  dataset.py     캐시 로드/병합, YmasDataset, DataLoader
  model.py       YmasNet, STGCNBlock, FocalLoss, EMA
  train.py       학습 루프 (EMA/SWA/도메인 균형 선택)
  evaluate.py    진단 + 임계값 스윕 + precision 우선 선택 + Holdout
  export.py      ONNX 내보내기
scripts/
  train_baseline.py   학습 진입점 (--config 로 실험 config 지정)
  diagnose_fa.py      오경보 해부 진단 (도메인/피험자/P(Fall) 분포)
  export_onnx.py      체크포인트 → ONNX
configs/
  v17_baseline.yaml   v13 그리드서치 확정 하이퍼파라미터 (재현 베이스라인)
  v18_aug.yaml        augmentation 강화 실험 (일반화 개선)
edge/                 Jetson Tier 2 실시간 추론 (ymas_realtime_ir.py, C++ 브리지, 캘리브레이션)
integration/          Tier 1 ↔ Tier 2 통합 (ymas_integrated.py, tier1_receiver)
firmware/             ESP32 Tier 1 펌웨어 (.ino)
hardware/             레일 플랫폼 CAD (OpenSCAD 소스 + STL + 프린트 가이드)
docs/
  handover.md         프로젝트 인수인계 (맥락 / 확정사항 / 폐기선택지 / 교훈)
  reference/          부품목록·종합정리 PDF (참고자료)
notebooks/
  colab_train.ipynb   Colab 실행 진입점 (git clone → import ymas)
```

## 학습 실행 (Colab / GPU)

```bash
git clone https://github.com/junparka-dotcom/Y-mas.git
cd Y-mas
pip install -r requirements.txt

python scripts/train_baseline.py \
  --ntu  /content/drive/MyDrive/.../ntu_ymas_v15_T64.npz \
  --etri /content/drive/MyDrive/.../etri_ymas_v15_T64.npz \
  --out  /content/drive/MyDrive/.../ckpt/ymas_v17_repro.pt
```

데이터 캐시(`*_v15_T64.npz`)는 Drive 에 보관하며 래포에는 커밋하지 않습니다
(`.gitignore`). 캐시가 있으면 원본 NTU/ETRI zip(수십 GB) 파싱이 불필요합니다.

## 개발 원칙 (인수인계 교훈)

1. **임계값은 precision≥0.95 게이트를 먼저 걸고 그 안에서 recall 최대화.**
   순서를 뒤집으면(“오경보 0 먼저”) 확률 절벽에서 recall 이 붕괴한다
   (실제 v16 에서 0.988 → 0.06).
2. **계산했다고 반영된 게 아니다.** SWA·평가·저장까지 반드시 검증한다.
3. 치수·스펙은 데이터시트/실물로 확인한다 (추측 금지).
4. Python 래퍼가 막히면 검증된 네이티브 경로로 내려간다.
5. **불변식:** `normalize_seq`, `physics_features`, `to_streams`, `pmean/pstd`
   는 학습·추론 코드에서 완전히 동일해야 한다.

자세한 배경은 [`docs/handover.md`](docs/handover.md) 참고.
