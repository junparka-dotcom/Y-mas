# Y-mas 프로젝트 인수인계

> 이 문서는 프로젝트 맥락(설계 근거·확정 사항·폐기 선택지·교훈)을 보존한다.
> 새 작업을 시작하기 전에 반드시 읽고, 여기 적힌 확정 사항과 폐기 선택지
> 안에서 논의할 것.

## 프로젝트 정체성

- **Y-mas** — 병실 침대 낙상 감지 시스템. 궁극 목표는 낙상 "감지"가 아니라
  **낙상 전 위험 예측(pre-fall)**, 나아가 침대 환자 상태 모니터링 **풀 베드
  솔루션**.
- 대학 경진대회 출품작 (AI반도체 전공 학부생).

## 시스템 아키텍처

| 단계 | 역할 | 구성 |
|---|---|---|
| Tier 1 | 상시 감시 · COG 편향 감지 | 로드셀 4 → HX711 4 → ESP32, 80Hz, UDP 트리거 |
| Tier 2 | 정밀 판정 | Femto W(ToF) → IR 주입력 → YOLO11n-pose → depth 3D 리프팅 → ST-GCN(v17) |
| Tier 3 | 알림 | 간호 스테이션 앱 (미구현) |

Tier 1 이 UDP 트리거를 보내면 Tier 2 가 `threading.Event` 로 깨어난다.

## 확정 사항 (변경 제안 불필요)

### 하드웨어
- 엣지 보드: NVIDIA Jetson Orin Nano Super 8GB, JetPack 6.2.1
- 카메라: Orbbec Femto W (ToF, Depth/IR) — **IR 이 주 입력** (소등 시 RGB 검출 0)
- MCU: ESP32-WROOM-32D
- 로드셀: CAS BCA-100L ×4 (150×35×**40**mm, 100kg, 2mV/V, IP65)
- 증폭: HX711 ×4, SCK 공유 결선, **RATE 핀 개조로 80Hz** (필수)
- 구조: 알루미늄 프로파일 30×60mm 레일 플랫폼

### Tier 2 모델 (v17)
- ST-GCN + Adaptive Graph + Multi-Scale TCN(3/5/9) + Temporal Attention Pool
  + Physics 10종 + Aux Fall Head
- trainable ~1.48M (state_dict 총합 1.50M, BN 버퍼 포함)
- Focal(γ=1.5), EMA(0.9999), Mixup(0.4), SWA, dropout=0.45, aux_weight=0.15,
  label_smooth=0.15
- 성능(문서 기준): th=0.75 에서 Holdout NTU recall 0.976, ETRI 0.988,
  오경보 79건(P39/P40 집중)
- 배포: ONNX opset 18 → TensorRT FP16, 평균 ≈2ms / ≈478 qps
- ONNX I/O: `skeleton[B,9,64,25]`, `physics[B,10]` → `logits[B,3]`

### 카메라 파이프라인
- IR/Depth 동일 물리 센서 공유 → Align 불필요
- `pyorbbecsdk` 는 0 채운 depth 만 반환 → C++ SDK v1.10.37 브릿지로 우회
  (Unix 도메인 소켓 스트리밍)
- zone 4점 캘리브레이션, 복합 판정(zone 이탈 AND tilt≥45° → Fall),
  EMA 스무딩(α=0.4) + 신뢰도 게이트(0.3)

## 폐기된 선택지 (다시 제안 금지)

### 데이터셋 — 전부 도메인 갭으로 실패
| 데이터셋 | 실패 원인 |
|---|---|
| Kaggle UR Fall | Depth 누락 |
| UP-Fall 3D | MediaPipe 33관절 → NTU 25관절 변환 시 성능 붕괴 |
| PKU-MMD v2 | 좌표계 불일치, precision 0.76 → 0.52 |
| FUKinect-Fall | Kinect V1 노이즈 전 백분위 3배, 필터 불가 |

**결론: 같은 센서 계열(Kinect V2 포맷) 데이터만 학습 투입 가능
→ 현재 NTU RGB+D 120 + ETRI-Activity3D 만 사용.**

### 기타 폐기
- 개별 캐스터 유닛 방식 → 레일 플랫폼으로 전환
- Nuitrack / K4A Body Tracking SDK (Femto W 미지원) → YOLO11n-pose + depth 리프팅
- 자탭 나사 → 볼트+육각너트 트랩 관통 체결
- 더 무거운 스켈레톤 모델(2s-AGCN, MS-G3D, CTR-GCN, ST-TR) — 엣지 실시간성 ·
  데이터 규모 대비 과적합 위험

## 뼈아팠던 교훈

1. **임계값은 precision 게이트(≥0.95)를 먼저 걸고 그 안에서 recall 최대화.**
   "오경보 0 먼저" 규칙이 확률 절벽 끝(th=0.95)을 골라 NTU recall 0.988 → 0.06 붕괴.
2. **계산했다고 반영된 게 아니다.** SWA 가중치를 계산만 하고 저장·평가하지
   않은 버그가 로그상 이상 없이 v13~v15 를 통과.
3. **치수는 데이터시트로 확인.** 로드셀 두께 22mm 가정 → 실제 40mm 로 기구 전면 재설계.
4. **Python 래퍼가 막히면 검증된 네이티브 경로로.** pyorbbecsdk → C++ 브릿지.
5. **불변식:** `normalize_seq`, `physics_features`, `to_streams`, `pmean/pstd` 는
   학습·추론에서 완전히 동일해야 한다.

## 데이터 현황 (캐시 실측)

| 캐시 | 샘플 | 클래스 분포 | 피험자 |
|---|---|---|---|
| `ntu_ymas_v15_T64.npz` (495MB) | 27,679 | Normal 20,114 / Risk 6,618 / Fall 947 | 54 |
| `etri_ymas_v15_T64.npz` (125MB) | 7,002 | Normal 5,168 / Risk 0 / Fall 1,834 (전부 A053) | 60 |

병합 34,681 → Train 18,771 / Val 9,803 / Holdout 6,107.

- pmean = [3.3124, 3.1606, 0.3925, 1.1291, 33.0563, 20.9063, 0.4188, 6.1961, 2.4740, 0.1158]
- pstd  = [7.8035, 5.5430, 0.5598, 0.5744, 27.6783, 22.7895, 0.5998, 9.3450, 0.6205, 0.1538]

### 데이터가 드러낸 약점 (개선 실험 표적)
1. **극심한 Fall 불균형** (Fall 2,781 vs Normal 25,282 ≈ 1:9) + 동적 낙상
   궤적(NTU A043)이 947개뿐. ETRI Fall 1,834 는 "쓰러지는 순간이 없는 정적 자세".
2. **Risk 는 NTU 단독** (ETRI 0) → 도메인 지름길 학습 위험.
3. 오경보가 특정 피험자(P39/P40)에 집중 → 하드 네거티브 문제.

## 미해결 상태

1. **실환경 도메인 갭 (가장 큼)** — v17 은 Kinect/NTU 만 학습해 Femto W 노이즈를
   본 적 없음. 정상 자세에서 P(Fall) 0.75~0.80. 근본 해결은 현 파이프라인으로
   **실데이터 수집 후 파인튜닝** (현재 카메라 수집 불가 상태).
2. 실측 미확정 치수 (캐스터 피치 1700mm, 레일 간격 560mm 등 — COG 식 직결 가정값).
3. Femto W USB 끊김 시 220V 재인가 필요 (무인 운용 리스크).
4. 미구현: Tier 3 앱, Tier 1 실물 제작 · end-to-end 통합, 풀 베드 확장.

## 로드맵

- **Phase 0 (현재)** — v17 노트북을 래포 모듈로 재구성 + 재현 베이스라인 확립.
- **Phase 1** — 캐시 기반 정직한 개선 실험 (동적 Fall 오버샘플, 하드 네거티브
  마이닝, precision 게이트 유지하며 recall↑ / 오경보↓). 동일 캐시·분할·불변식,
  Val 로만 튜닝, Holdout 은 마지막 1회.
- **Phase 2 (하드웨어 준비 후)** — Femto W 실데이터 수집 → 파인튜닝으로 도메인 갭 해소.
