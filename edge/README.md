# edge/ — Jetson Tier 2 실시간 추론

Orbbec Femto W(ToF) → IR/Depth 캡처 → YOLO11n-pose → depth 3D 리프팅 →
NTU 25관절 매핑 → ST-GCN(v17) 낙상 판정. Jetson Orin Nano Super 에서 구동.

## 파일

| 파일 | 역할 |
|---|---|
| `ymas_realtime_ir.py` | **주 실행 파일.** IR 주입력 실시간 통합 추론 (포즈→뎁스 리프팅→조인트 매핑→v17→zone 필터). `normalize_seq`/`physics_features`/`to_streams`/PMEAN/PSTD 가 학습 코드와 동일 |
| `ymas_realtime.py` | 이전 버전(RGB-D 경로). IR 경로로 대체됨 — 참고용 |
| `bed_calibrate.py` | IR 화면에서 침대 4모서리 클릭 → `bed_region.json` 생성 |
| `bed_region.json` | 침대 zone 폴리곤 (픽셀 좌표). zone 이탈 판정에 사용 |
| `watchdog.sh` | 브리지 감시·자동 재시작 (무개입 복구) |
| `bridges/` | C++ depth/IR 캡처 브리지 (Unix 도메인 소켓 스트리밍) + 파이썬 클라이언트/진단 |
| `tests/` | ONNX/TensorRT 엔진 검증, 카메라 스트림 테스트 |

## bridges/

`pyorbbecsdk` 가 0 으로 채운 depth 만 반환하는 문제 때문에 C++ SDK(v1.10.37)로
직접 캡처해 Unix 도메인 소켓(`/tmp/ymas_depth.sock`)으로 파이썬에 스트리밍한다.

| 파일 | 역할 |
|---|---|
| `ir_depth_bridge.cpp` | **주 브리지.** IR+Depth 동시 캡처, `select()` 재접속 지원 |
| `ir_bridge.cpp` / `depth_bridge.cpp` / `rgbd_bridge.cpp` | 단일 스트림/실험용 브리지 |
| `*_client.py`, `rgbd_diag*.py` | 브리지 수신 클라이언트 및 진단 |

## 불변식 주의

`ymas_realtime_ir.py` 의 전처리 함수와 PMEAN/PSTD 는 학습 코드
(`ymas/preprocess.py`, 체크포인트의 pmean/pstd)와 **완전히 동일해야 한다.**
모델을 v18 이상으로 갱신하면 이 파일의 PMEAN/PSTD/TH_FALL 도 함께 맞출 것.
