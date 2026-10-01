# Tier 2 배포 가이드 — Jetson AGX Orin

ST-GCN v17 낙상 모델을 AGX Orin(JetPack 6.2.3, CUDA 12.6, TensorRT 10.3)에 배포하는 절차.
모델 파일은 래포에 없으므로(gitignore) **Colab/Drive에서 가져온다.**

> 실측(2026-10-01): 엔진 벤치 ~1ms/추론(≈988 qps), 실시간 파이프라인 FPS 30
> (YOLO11n-pose+ST-GCN 모두 CPU onnxruntime), FALL ALERT 까지 end-to-end 검증 완료.
> GPU(TensorRT) 추론 전환은 추후 최적화 과제.

> 이 문서의 명령은 **모두 AGX Orin 에서** 실행한다(`ssh ymas@192.168.0.9`).
> 모델 I/O: `skeleton[1,9,64,25]`, `physics[1,10]` → `logits[1,3]` (T=64, V=25, n_phys=10)

---

## 0. 준비 — 코드/모델 가져오기

### 0-1. 래포 클론(또는 최신화)
```bash
cd ~
git clone https://github.com/junparka-dotcom/Y-mas.git   # 최초 1회
# 이미 있으면:  cd ~/Y-mas && git pull
cd ~/Y-mas
```

### 0-2. 모델 파일 가져오기 (Drive → Jetson)
Colab 학습 산출물은 Google Drive 에 있다. 필요한 파일:
- `ymas_v17.onnx` (+ `ymas_v17.onnx.data` external weight — **반드시 같이**)
- `validation_samples_v17.npz` (엔진 검증용, Colab CELL 에서 생성)

Drive 에서 받는 방법(택1):
```bash
# (A) 브라우저로 Drive 에서 다운로드 후 scp/USB 로 옮기기
# (B) gdown 사용 (파일 공유링크 ID 필요)
pip install --user gdown
gdown 'https://drive.google.com/uc?id=<FILE_ID>' -O ymas_v17.onnx
gdown 'https://drive.google.com/uc?id=<FILE_ID>' -O ymas_v17.onnx.data
gdown 'https://drive.google.com/uc?id=<FILE_ID>' -O validation_samples_v17.npz
```
작업 폴더 예: `~/Y-mas/edge/` 에 세 파일을 둔다.

---

## 1. 환경 점검
```bash
# JetPack / CUDA / TensorRT 버전
dpkg -l | grep nvidia-jetpack | head -1
nvcc --version
dpkg -l | grep -i tensorrt | grep -i libnvinfer-bin || /usr/src/tensorrt/bin/trtexec --help | head -1
python3 -c "import tensorrt as trt; print('TensorRT', trt.__version__)"

# 전력 최대 모드(빌드/벤치 시에만)
sudo nvpmodel -m 0 && sudo jetson_clocks
```
기대: JetPack 6.2.3, CUDA 12.6, TensorRT 10.x.

---

## 2. TensorRT FP16 엔진 빌드
```bash
cd ~/Y-mas/edge
/usr/src/tensorrt/bin/trtexec \
  --onnx=ymas_v17.onnx \
  --saveEngine=ymas_v17_fp16.engine \
  --fp16 \
  --shapes=skeleton:1x9x64x25,physics:1x10
```
- 성공 시 `ymas_v17_fp16.engine` 생성 + "PASSED" 로그.
- `.onnx.data` 가 같은 폴더에 없으면 weight 로드 실패 → 0-2 확인.

---

## 3. 엔진 검증 (PyTorch 로짓과 비교)
```bash
# 검증 스크립트는 cuda-python(from cuda import cudart) 필요. 재플래시 후엔 없음.
# ⚠️ cuda-python 13.x 는 API 가 cuda.bindings 로 이동해 import 가 깨진다.
#    시스템 CUDA 12.6 에 맞춰 12.x 를 설치할 것:
pip install --user "cuda-python>=12.6,<13"

cd ~/Y-mas/edge/tests
# 검증 스크립트는 ymas_v17_fp16.engine / validation_samples_v17.npz 를 현재 폴더에서 찾음
cp ../ymas_v17_fp16.engine .
cp ../validation_samples_v17.npz .
python3 validate_engine.py
```
기대 출력: 예측 클래스 전체 일치(YES), 평균 max|diff| 작음(FP16 반올림 오차 범위).
> ⚠️ npz 의 physics 차원이 8이면 현재 모델(n_phys=10)과 불일치 → Colab 에서
> n_phys=10 기준으로 `validation_samples_v17.npz` 를 재생성할 것.

---

## 4. 성능 벤치 (추론 속도)
```bash
# 엔진만으로 순수 추론 처리량 측정
/usr/src/tensorrt/bin/trtexec \
  --loadEngine=ymas_v17_fp16.engine \
  --shapes=skeleton:1x9x64x25,physics:1x10 \
  --iterations=1000 --avgRuns=100
```
- `mean`(ms/inference), `throughput`(qps) 확인.
- 기준(Nano Super): v17 ≈2ms / ≈478 qps. **AGX Orin 은 이보다 빠를 것.**

---

## 5. Orbbec C++ 브리지 빌드 (카메라)
> 카메라(Femto W) 연결 후 진행. 하드웨어 없으면 4단계까지만.

### 5-1. 카메라 인식 확인
```bash
lsusb | grep -i -E "orbbec|2bc5"   # 2bc5:0638 Orbbec Femto W Camera 가 보여야 함
```

### 5-2. Orbbec SDK v1.10.37 (arm64) 설치
재플래시 후에는 SDK 가 없다. GitHub 릴리스에서 **linux_arm64** zip 을 받는다:
```bash
mkdir -p ~/orbbec_sdk && cd ~/orbbec_sdk
wget -O OrbbecSDK_arm64.zip "https://github.com/orbbec/OrbbecSDK/releases/download/v1.10.37/OrbbecSDK_C_C%2B%2B_v1.10.37_20260707_3f75820_linux_arm64_release.zip"
sudo apt-get install -y unzip   # 없으면
unzip -o OrbbecSDK_arm64.zip
```
> ⚠️ 압축 해제 후 include/lib 는 최상위가 아니라 **`OrbbecSDK_v1.10.37/SDK/`** 하위에 있다.
> - 헤더: `OrbbecSDK_v1.10.37/SDK/include/libobsensor/ObSensor.hpp`
> - 라이브러리: `OrbbecSDK_v1.10.37/SDK/lib/libOrbbecSDK.so`

### 5-3. udev 규칙 설치 (USB 권한 — 필수)
안 하면 브리지가 카메라를 못 연다. 설치 후 **카메라 USB 를 뽑았다 다시 꽂는다.**
```bash
cd ~/orbbec_sdk/OrbbecSDK_v1.10.37/Script
sudo bash install_udev_rules.sh
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 5-4. 브리지 컴파일
```bash
cd ~/Y-mas/edge/bridges
SDK=~/orbbec_sdk/OrbbecSDK_v1.10.37/SDK
g++ -std=c++17 ir_depth_bridge.cpp -o ir_depth_bridge \
  -I"$SDK/include" \
  -L"$SDK/lib" -lOrbbecSDK -lpthread \
  -Wl,-rpath,"$SDK/lib"          # ← rpath 필수(런타임에 .so 를 찾게)
# 실행: IR+Depth 캡처 → /tmp/ymas_irdepth.sock 스트리밍
./ir_depth_bridge
```
성공하면 `depth intrinsics: fx=... fy=...` 에 실제 숫자가 찍히고
`waiting for python client...` 로 대기한다.
> ⚠️ 소켓 경로는 **`/tmp/ymas_irdepth.sock`** 이다(실제 브리지/클라이언트 코드 기준).
> - `pyorbbecsdk`(파이썬 바인딩)는 0 채운 depth 만 반환하므로 **C++ 브리지 필수.**
> - `watchdog.sh` 로 브리지 자동 재시작 감시 가능.

---

## 6. 실시간 통합 추론

### 6-1. 파이썬 의존성 (재플래시 후 필요)
```bash
pip install --user onnxruntime          # CPU 판. Jetson GPU 가속은 별도 wheel 필요(아래 참고)
pip install --user "numpy<2"            # ⚠️ 시스템 cv2 4.8 은 numpy 1.x 로 빌드됨. numpy 2.x 면 cv2 import 깨짐
# cv2 는 JetPack 기본 opencv(4.8) 사용. import 되는지 확인:
python3 -c "import onnxruntime, cv2, numpy; print('deps OK', cv2.__version__, numpy.__version__)"
```
> 🚫 **Orin 에서 `pip install ultralytics` 금지.** 범용 torch 2.x + `nvidia-*-cu13` 뭉치(수 GB)를
> 끌고 와 JetPack(CUDA 12.6) 환경을 오염시키고 eMMC 를 치먹으며 numpy 를 2.x 로 올려
> cv2/`cuda-python` 을 깨뜨린다. YOLO ONNX 는 **Colab 에서 export** 한다(아래 6-2).

### 6-2. YOLO11n-pose ONNX 확보 (Colab)
`edge/yolo11n-pose.onnx` 는 래포에 없다(gitignore). `notebooks/ymas_yolo11n_pose_export.ipynb`
를 Colab(무료 T4)에서 실행해 export → Drive 저장 → Orin 으로 옮긴다:
```bash
mv ~/Downloads/yolo11n-pose.onnx ~/Y-mas/edge/
```
> export 는 `imgsz=320`(코드 `YOLO_SIZE` 와 일치), `opset=12`.
> 출력 shape 은 `(1, 56, 2100)` (imgsz 320 기준; 640 이면 8400). 실시간 코드 파싱과 호환.

### 6-3. 필요한 모델 파일 (모두 `~/Y-mas/edge/`)
- `yolo11n-pose.onnx` (Colab export, 6-2)
- `ymas_v17.onnx` (+ `.onnx.data`, 0-2)
- `bed_region.json` (아래 캘리브레이션 또는 래포 동봉본)

### 6-4. 실행 (터미널 2개)
```bash
# 터미널 A — 브리지 (카메라 캡처, 먼저)
cd ~/Y-mas/edge/bridges && ./ir_depth_bridge

# 터미널 B — 실시간 추론
cd ~/Y-mas/edge
python3 bed_calibrate.py       # 침대 zone 4점 캘리브레이션(최초 1회) → bed_region.json
python3 ymas_realtime_ir.py    # 브리지가 /tmp/ymas_irdepth.sock 스트리밍 중이어야 함
```
- 모델/로그 경로는 스크립트(`edge/`) 기준으로 자동 해석된다(하드코딩 제거됨).
  필요 시 `YMAS_POSE_ONNX`/`YMAS_FALL_ONNX`/`YMAS_BED_JSON`/`YMAS_LOG_DIR` 로 override.
- `cv2.imshow` GUI 창이 뜬다 → Orin 로컬 디스플레이 필요(순수 SSH 면 표시 안 됨).
- `ymas_realtime_ir.py` 의 PMEAN/PSTD/전처리는 학습 코드와 **동일해야** 함(불변식).
- 모델 갱신 시 PMEAN/PSTD/TH_FALL 도 함께 맞출 것.
- 정상 판정 로그 예:
  `buf=15 zone=in tilt_final=51.0 [EXIT+TILT] P(Fall)=.. -> Fall *** FALL ALERT ***`

---

## 6-B. (최적화) GPU 추론 — TensorRT 백엔드

기본은 YOLO11n-pose / ST-GCN 을 **CPU onnxruntime** 으로 돌린다(30fps 나옴).
아래처럼 **TensorRT 엔진**을 만들어 두면 `ymas_realtime_ir.py` 가 자동으로 GPU 를 쓴다
(엔진 없으면 onnxruntime 으로 폴백, `YMAS_FORCE_ONNX=1` 이면 강제 CPU).

```bash
cd ~/Y-mas/edge
# (빌드 중엔 MAXN 권장)  sudo nvpmodel -m 0 && sudo jetson_clocks

# YOLO11n-pose ONNX → FP16 엔진 (입력 고정 1x3x320x320 → --shapes 불필요)
/usr/src/tensorrt/bin/trtexec \
  --onnx=yolo11n-pose.onnx \
  --saveEngine=yolo11n-pose_fp16.engine --fp16
# ST-GCN 엔진은 2단계에서 이미 빌드함(ymas_v17_fp16.engine)

# 엔진 텐서 이름 확인용(YOLO: images/output0, STGCN: skeleton,physics/logits)
python3 -c "import tensorrt as trt; l=trt.Logger();
import sys; f=open('yolo11n-pose_fp16.engine','rb'); e=trt.Runtime(l).deserialize_cuda_engine(f.read());
print([(e.get_tensor_name(i), e.get_tensor_mode(e.get_tensor_name(i))) for i in range(e.num_io_tensors)])"
```

- 실행은 6-4 와 동일(`python3 ymas_realtime_ir.py`). 시작 로그에
  `pose backend: TensorRT` / `fall backend: TensorRT` 가 뜨면 GPU 사용 중.
- 헬퍼: `edge/trt_infer.py`(cuda-python 기반, onnxruntime.run 유사 규약).
- 실측(50W 모드): FPS 30 유지, `tegrastats` 상 CPU 코어 대부분 <20%, GR3D(GPU) 활성.
  YOLO 를 GPU 로 넘겨 CPU 여유가 크게 확보됨(멀티카메라/추가로직 여지).
- FP16 이라 확률값은 소수점 미세차 가능하나 판정 클래스는 CPU 와 동일해야 함.
  비교하려면 `YMAS_FORCE_ONNX=1 python3 ymas_realtime_ir.py` 로 CPU 강제.

---

## 7. 마치고 — 전력 원복
```bash
sudo jetson_clocks --restore
sudo nvpmodel -m 3      # 평소 50W 모드
```

---

## 8. 무인 운영 — systemd 부팅 자동실행/자동복구

24시간 무인 낙상 감지를 위해 브리지+추론을 systemd 서비스로 등록한다.
부팅 시 자동 시작 + 프로세스가 죽으면 자동 재시작(`Restart=always`).

### 8-1. 헤드리스 모드
서비스는 GUI(디스플레이)가 없으므로 실시간 스크립트가 헤드리스로 동작해야 한다.
`ymas_realtime_ir.py` 는 `YMAS_HEADLESS=1`(또는 `DISPLAY` 미설정)이면 자동으로
`cv2.imshow`/렌더를 생략하고 **로그/경보만** 남긴다(30초 하트비트 로그 포함).
GUI 로 보고 싶으면 그냥 데스크톱 터미널에서 직접 실행하면 된다.

### 8-2. 설치 (서비스 등록 + 자동시작)
```bash
cd ~/Y-mas/edge/systemd
sudo bash install_services.sh
```
스크립트가 하는 일: 유닛 파일을 `/etc/systemd/system/` 로 복사 →
`daemon-reload` → `enable`(부팅 자동시작) → `start`.

두 서비스:
| 서비스 | 역할 | 의존 |
|---|---|---|
| `ymas-bridge.service`   | Orbbec C++ 브리지(카메라 캡처) | - |
| `ymas-realtime.service` | 헤드리스 추론(낙상 판정)        | bridge 를 Requires/After |

### 8-3. 상태/로그 확인
```bash
systemctl status ymas-bridge ymas-realtime
journalctl -u ymas-realtime -f    # 실시간 판정/하트비트 로그
journalctl -u ymas-bridge -f      # 브리지(카메라) 로그
```

### 8-4. 재시작/중지/제거
```bash
sudo systemctl restart ymas-realtime          # 추론만 재시작
sudo systemctl restart ymas-bridge            # 브리지 재시작(추론도 함께 영향)
sudo systemctl disable --now ymas-realtime ymas-bridge   # 자동시작 해제 + 중지
```

### 8-5. 부팅 자동실행 검증
```bash
sudo reboot
# 재부팅 후(로그인 없이도 떠 있어야 함):
systemctl is-active ymas-bridge ymas-realtime   # 둘 다 active
journalctl -u ymas-realtime -n 20               # 판정/하트비트 로그 확인
```

> 참고: `edge/watchdog.sh` 는 systemd 가 없는 환경용 폴백(브리지만 감시)이다.
> systemd 를 쓰면 8단계로 대체된다(브리지+추론 모두 감시/복구).
> GPU 엔진(6-B)이 있으면 서비스도 자동으로 TensorRT 를 쓴다.

---

## 9. Tier 3 연동 — 간호 스테이션 대시보드 알림

Tier 2(이 보드)가 낙상을 확정하면 Tier 3 알림 서버(`tier3/`)로 `FallEvent` 를
POST → 간호 스테이션 브라우저 대시보드에 **실시간 빨강 경보**가 뜬다.

```
[ymas_realtime_ir.py] --낙상 확정(POST /ingest)--> [Tier3 FastAPI :8000] --WS--> [대시보드 브라우저]
```

### 9-1. 동작 방식 (코드)
`ymas_realtime_ir.py` 는 `YMAS_TIER3_URL` 이 설정됐을 때만 낙상(`pred_class==2`)
시 알림을 보낸다. 미설정이면 Tier 2 단독 동작(기존과 동일).
- **비차단**: 별도 데몬 스레드 + 2초 타임아웃 → 서버가 느리거나 죽어도 추론은 계속
- **쿨다운**: `YMAS_TIER3_COOLDOWN`(기본 10초)로 FALL 알림 폭주 방지
- 환경변수: `YMAS_TIER3_URL`(예: `http://localhost:8000`), `YMAS_BED_ID`(기본 `301-A`)

### 9-2. 서버 의존성 설치
```bash
pip install --user -r ~/Y-mas/tier3/requirements-tier3.txt   # fastapi, uvicorn
```

### 9-3. systemd 로 함께 운영 (8단계에 포함됨)
`install_services.sh` 는 Tier3 서버(`ymas-tier3.service`)도 함께 설치하며,
`ymas-realtime.service` 에 `YMAS_TIER3_URL=http://localhost:8000` 이 설정돼 있다.
8단계 설치를 하면 bridge + tier3 + realtime 3개가 함께 뜬다.
```bash
cd ~/Y-mas/edge/systemd && sudo bash install_services.sh
# 대시보드:  http://localhost:8000  (오린 로컬) 또는 http://<오린IP>:8000 (간호 스테이션 PC)
journalctl -u ymas-realtime -f    # 낙상 시 [tier3] FALL 알림 전송 로그 확인
journalctl -u ymas-tier3 -f       # 서버 수신 로그
```

### 9-4. 수동 실행(테스트용, systemd 없이)
```bash
# 터미널 1: Tier3 서버
cd ~/Y-mas/tier3/server && python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
# 터미널 2: 브리지
cd ~/Y-mas/edge/bridges && ./ir_depth_bridge
# 터미널 3: 실시간 추론 (Tier3 전송 켜기)
cd ~/Y-mas/edge && YMAS_TIER3_URL=http://localhost:8000 YMAS_BED_ID=301-A python3 ymas_realtime_ir.py
```
하드웨어 없이 대시보드만 먼저 보려면: `python3 ~/Y-mas/tier3/mock_tier2.py --scenario fall`

### 9-5. 서버를 별도 간호 스테이션 PC 에 둘 때
Tier3 서버를 다른 PC 에서 돌리면, `ymas-realtime.service` 의
`YMAS_TIER3_URL` 을 그 PC IP 로 바꾼다(예: `http://192.168.0.50:8000`).
서버는 `--host 0.0.0.0` 이라 같은 네트워크면 브라우저로 접속 가능.

---

## 트러블슈팅
| 증상 | 원인/해결 |
|---|---|
| trtexec weight 로드 실패 | `ymas_v17.onnx.data` 가 onnx 와 같은 폴더에 있어야 함 |
| `import tensorrt` 실패 | JetPack 기본 파이썬(python3) 사용. venv 면 `--system-site-packages` |
| PyTorch CPU만 됨 | `pip install torch` 금지 → JetPack 6.2.3용 NVIDIA wheel/컨테이너 |
| validate physics 차원 오류 | npz(physics=8) vs 모델(n_phys=10) → npz 재생성 |
| 엔진 로드 실패(버전) | 엔진은 빌드한 그 TensorRT 버전에서만 로드됨. Jetson 에서 직접 빌드할 것 |
| `from cuda import cudart` 실패 | cuda-python 13.x 는 `cuda.bindings` 로 이동. `pip install "cuda-python>=12.6,<13"` |
| `cv2` import 시 numpy 오류 | numpy 2.x ↔ 시스템 cv2 4.8(numpy 1.x 빌드) 충돌 → `pip uninstall numpy` 로 시스템 1.21 복귀 or `pip install "numpy<2"` |
| ultralytics 설치 후 환경 오염 | Orin 에서 설치 금지. torch/cu13 뭉치가 JetPack 오염 + cuda-python 깨짐. YOLO export 는 Colab 에서 |
| 브리지 컴파일 시 `ObSensor.hpp` 없음 | include 는 `SDK/include` 하위. `-I$SDK/include` 로 지정 |
| 브리지 실행 시 `.so` 못 찾음 | 컴파일에 `-Wl,-rpath,$SDK/lib` 추가 |
| 브리지 카메라 오픈 실패/권한 | udev 규칙 설치 후 USB 재삽입(5-3) |
| 실시간 `cv2.imshow` display 오류 | Orin 로컬 디스플레이 필요. 순수 SSH 면 GUI 표시 안 됨 |
| 모델 경로 못 찾음 | 경로 하드코딩 제거됨(스크립트 기준 자동). `YMAS_POSE_ONNX` 등 env 로 override 가능 |
| systemd 서비스가 GUI 오류로 죽음 | 서비스는 헤드리스여야 함. `YMAS_HEADLESS=1` 이 유닛에 설정돼 있음(8-1) |
| 서비스에서 cuda-python/onnxruntime 못 찾음 | pip `--user` 설치라 `HOME=/home/ymas` 가 유닛에 설정돼 `~/.local` 인식(8-2) |
| 서비스 계속 재시작 반복 | `journalctl -u ymas-realtime -n 50` 로 원인 확인. 모델/엔진 파일 존재 여부부터 |
| 재부팅 후 안 뜸 | `systemctl is-enabled ymas-bridge ymas-realtime` 확인. 아니면 `enable`(8-2) |
| 대시보드에 낙상 안 뜸 | realtime 로그에 `[tier3] FALL 알림 전송` 뜨는지 확인. 안 뜨면 `YMAS_TIER3_URL` 미설정 |
| `[tier3] 전송 실패` 로그 | Tier3 서버 미기동/방화벽. `journalctl -u ymas-tier3`, `curl localhost:8000/healthz` |
| ymas-tier3 안 뜸 | fastapi/uvicorn 미설치. `pip install --user -r ~/Y-mas/tier3/requirements-tier3.txt`(9-2) |
| 다른 PC 에서 대시보드 접속 안 됨 | 서버는 `0.0.0.0:8000`. 오린 방화벽/네트워크 확인, URL은 오린 IP(192.168.0.9) |
