# Tier 2 배포 가이드 — Jetson AGX Orin

ST-GCN v17 낙상 모델을 AGX Orin(JetPack 6.2.3, CUDA 12.6)에 배포하는 절차.
모델 파일은 래포에 없으므로(gitignore) **Colab/Drive에서 가져온다.**

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

```bash
# Orbbec SDK v1.10.37 (C++) 설치 후 (SDK 경로는 설치 위치에 맞게)
cd ~/Y-mas/edge/bridges
g++ -std=c++17 ir_depth_bridge.cpp -o ir_depth_bridge \
  -I<ORBBEC_SDK>/include -L<ORBBEC_SDK>/lib -lOrbbecSDK -lpthread
# 실행: IR+Depth 캡처 → /tmp/ymas_depth.sock 스트리밍
./ir_depth_bridge
```
- `pyorbbecsdk`(파이썬 바인딩)는 0 채운 depth 만 반환하므로 **C++ 브리지 필수.**
- `watchdog.sh` 로 브리지 자동 재시작 감시 가능.

---

## 6. 실시간 통합 추론
```bash
cd ~/Y-mas/edge
# 침대 zone 4점 캘리브레이션(최초 1회) → bed_region.json 생성
python3 bed_calibrate.py
# 실시간 추론 (브리지가 /tmp/ymas_depth.sock 스트리밍 중이어야 함)
python3 ymas_realtime_ir.py
```
- `ymas_realtime_ir.py` 의 PMEAN/PSTD/전처리는 학습 코드와 **동일해야** 함(불변식).
- 모델 갱신 시 PMEAN/PSTD/TH_FALL 도 함께 맞출 것.

---

## 7. 마치고 — 전력 원복
```bash
sudo jetson_clocks --restore
sudo nvpmodel -m 3      # 평소 50W 모드
```

---

## 트러블슈팅
| 증상 | 원인/해결 |
|---|---|
| trtexec weight 로드 실패 | `ymas_v17.onnx.data` 가 onnx 와 같은 폴더에 있어야 함 |
| `import tensorrt` 실패 | JetPack 기본 파이썬(python3) 사용. venv 면 `--system-site-packages` |
| PyTorch CPU만 됨 | `pip install torch` 금지 → JetPack 6.2.3용 NVIDIA wheel/컨테이너 |
| validate physics 차원 오류 | npz(physics=8) vs 모델(n_phys=10) → npz 재생성 |
| 엔진 로드 실패(버전) | 엔진은 빌드한 그 TensorRT 버전에서만 로드됨. Jetson 에서 직접 빌드할 것 |
