#!/usr/bin/env python3
"""
=============================================================
 Y-mas 통합 실행 스크립트 (Jetson)

 Tier 1 (하중) + Tier 2 (비전 ST-GCN v13) 병합 완성본.

 흐름
   ESP32 --LAN UDP--> [수신 스레드] --인터럽트--> [비전 스레드]
                                                      |
                              Orbbec 카메라 -> 25관절 -> ST-GCN
                                                      |
                                              낙상 확정 -> MQTT 알람

 실행
   실물  : python3 ymas_integrated.py --udp 5005 --onnx ymas_v13.onnx
   모의  : python3 ymas_integrated.py --sim --mock-camera
=============================================================
"""
import argparse, csv, json, math, os, socket, threading, time
from collections import deque
from datetime import datetime

import numpy as np

# =============================================================
# 1. Tier 2 설정 -- v13 학습 코드와 반드시 일치해야 함
# =============================================================
T_WINDOW   = 64        # CFG['T']
N_JOINT    = 25
FPS        = 30.0
TH_FALL    = 0.7       # v13 확정 운영 임계값
TH_RISK    = 0.4
CONFIRM_N  = 2
STRIDE     = 2
EMA_ALPHA  = 0.5
CLASS_NAMES = ['Normal', 'Risk', 'Fall']

# 관절 인덱스 (학습 코드와 동일)
J_SPINE_BASE, J_HEAD = 0, 3
J_SHL, J_SHR, J_SPINE_SHOULDER = 4, 8, 20

NTU_BONES = [
    (1,2),(2,21),(3,21),(4,3),(5,21),(6,5),(7,6),(8,7),
    (9,21),(10,9),(11,10),(12,11),(13,1),(14,13),(15,14),
    (16,15),(17,1),(18,17),(19,18),(20,19),(21,2),
    (22,23),(23,8),(24,25),(25,12),
]
BONE_PAIRS = [(a-1, b-1) for a, b in NTU_BONES]


# =============================================================
# 2. 전처리 -- 학습 시 사용한 함수를 그대로 옮긴 것
#    이 세 함수가 학습 코드와 다르면 정확도가 무너집니다.
# =============================================================
def normalize_seq(seq):
    """중력축(Y) 보존 정규화. 원점이동 -> Yaw 회전 -> 몸통 스케일"""
    seq = seq.copy().astype(np.float32)
    seq -= seq[0, J_SPINE_BASE].copy()
    sh = seq[0, J_SHR] - seq[0, J_SHL]
    theta = np.arctan2(sh[2], sh[0])
    c, s = np.cos(-theta), np.sin(-theta)
    R = np.array([[c,0,-s],[0,1,0],[s,0,c]], dtype=np.float32)
    seq = seq @ R.T
    torso = np.linalg.norm(seq[0, J_SPINE_SHOULDER] - seq[0, J_SPINE_BASE])
    if torso < 1e-3:
        torso = 1.0
    return (seq / torso).astype(np.float32)


def physics_features(seq, fps=FPS):
    """물리 특징 8개"""
    dt = 1.0 / fps
    hy = seq[:, J_HEAD, 1]
    sy = seq[:, J_SPINE_BASE, 1]
    vh = -np.diff(hy) / dt
    vs = -np.diff(sy) / dt
    body = seq[:, J_HEAD] - seq[:, J_SPINE_BASE]
    n = np.linalg.norm(body, axis=1) + 1e-6
    tilt = np.degrees(np.arccos(np.clip(body[:, 1] / n, -1.0, 1.0)))
    horiz = np.linalg.norm(seq[:, J_SPINE_BASE][:, [0, 2]], axis=1)
    motion = np.linalg.norm(np.diff(seq, axis=0), axis=2).mean(axis=1) / dt
    return np.array([
        vh.max(), vs.max(),
        hy[0] - hy.min(), hy[-5:].mean(),
        tilt.max(), tilt[-5:].mean(),
        horiz.max(), motion.max(),
    ], dtype=np.float32)


def to_streams(x):
    """(T,V,3) -> (9,T,V)  joint | bone | velocity"""
    joint = x.copy()
    bone = np.zeros_like(x)
    for a, b in BONE_PAIRS:
        bone[:, a] = x[:, a] - x[:, b]
    vel = np.zeros_like(x)
    vel[1:] = x[1:] - x[:-1]
    return np.concatenate([joint, bone, vel], axis=2).transpose(2, 0, 1)


# =============================================================
# 3. ST-GCN 추론 엔진
#    TensorRT -> ONNXRuntime -> Mock 순으로 자동 선택
# =============================================================
class FallClassifier:
    def __init__(self, onnx_path=None, trt_path=None, pmean=None, pstd=None):
        self.backend = 'mock'
        self.session = None
        self.pmean = np.zeros(8, np.float32) if pmean is None else pmean
        self.pstd  = np.ones(8, np.float32)  if pstd  is None else pstd

        if trt_path and os.path.exists(trt_path):
            if self._init_trt(trt_path):
                return
        if onnx_path and os.path.exists(onnx_path):
            if self._init_onnx(onnx_path):
                return
        print("[분류기] 모델 없음 -> Mock 모드 (물리 특징 기반 근사 판정)")

    def _init_trt(self, path):
        try:
            import tensorrt as trt
            import pycuda.driver as cuda
            import pycuda.autoinit          # noqa: F401
            logger = trt.Logger(trt.Logger.WARNING)
            with open(path, 'rb') as f, trt.Runtime(logger) as rt:
                self.engine = rt.deserialize_cuda_engine(f.read())
            self.context = self.engine.create_execution_context()
            self.cuda = cuda
            self.backend = 'tensorrt'
            print(f"[분류기] TensorRT 엔진 로드: {path}")
            return True
        except Exception as e:
            print(f"[분류기] TensorRT 실패: {e}")
            return False

    def _init_onnx(self, path):
        try:
            import onnxruntime as ort
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            avail = ort.get_available_providers()
            providers = [p for p in providers if p in avail]
            self.session = ort.InferenceSession(path, providers=providers)
            self.in_names = [i.name for i in self.session.get_inputs()]
            self.backend = 'onnx'
            print(f"[분류기] ONNX 로드: {path}  ({providers[0]})")
            return True
        except Exception as e:
            print(f"[분류기] ONNX 실패: {e}")
            return False

    def predict(self, seq_raw):
        """seq_raw: (T,25,3) 원본 좌표 -> softmax 확률 (3,)"""
        seq = normalize_seq(seq_raw)
        feat = (physics_features(seq) - self.pmean) / self.pstd
        x = to_streams(seq)[None].astype(np.float32)    # (1,9,T,25)
        p = feat[None].astype(np.float32)               # (1,8)

        if self.backend == 'onnx':
            out = self.session.run(None, {self.in_names[0]: x,
                                          self.in_names[1]: p})[0][0]
            return self._softmax(out)
        if self.backend == 'tensorrt':
            return self._infer_trt(x, p)
        return self._mock(feat)

    def _infer_trt(self, x, p):
        cuda = self.cuda
        d_x = cuda.mem_alloc(x.nbytes)
        d_p = cuda.mem_alloc(p.nbytes)
        out = np.empty((1, 3), dtype=np.float32)
        d_o = cuda.mem_alloc(out.nbytes)
        cuda.memcpy_htod(d_x, x)
        cuda.memcpy_htod(d_p, p)
        self.context.execute_v2([int(d_x), int(d_p), int(d_o)])
        cuda.memcpy_dtoh(out, d_o)
        return self._softmax(out[0])

    @staticmethod
    def _softmax(v):
        e = np.exp(v - v.max())
        return e / e.sum()

    @staticmethod
    def _mock(feat):
        """
        모델 없을 때 물리 특징으로 근사 판정.
        배선/카메라 검증용이며 실제 배포에는 쓰지 마십시오.
        feat 는 정규화된 값이므로 대략적인 크기만 봅니다.
        """
        v_head, drop, final_h, tilt_max = feat[0], feat[2], feat[3], feat[4]
        score = 0.0
        if v_head   > 1.5:  score += 0.35
        if drop     > 1.0:  score += 0.30
        if tilt_max > 45.0: score += 0.20
        if final_h  < -0.3: score += 0.15
        score = min(score, 0.95)
        # prob[2] 가 곧 낙상 확률. th_fall 과 직접 비교되므로 스케일하지 않습니다.
        rest = 1.0 - score
        return np.array([rest * 0.7, rest * 0.3, score], np.float32)


# =============================================================
# 4. Tier 2 런타임 -- v13 CELL 10 의 YmasRuntime 을 이식
# =============================================================
class YmasRuntime:
    def __init__(self, clf, T=T_WINDOW, stride=STRIDE,
                 th_fall=TH_FALL, th_risk=TH_RISK,
                 confirm_n=CONFIRM_N, alpha=EMA_ALPHA):
        self.clf = clf
        self.buf = deque(maxlen=T)
        self.T, self.stride = T, stride
        self.th_fall, self.th_risk = th_fall, th_risk
        self.confirm_n, self.alpha = confirm_n, alpha
        self.ema = np.zeros(3, np.float32)
        self.hits = 0
        self.k = 0
        self.n_infer = 0

    def reset(self):
        self.ema[:] = 0
        self.hits = 0
        self.k = 0
        self.n_infer = 0

    def push(self, frame):
        """frame: (25,3) 카메라에서 나온 원본 좌표"""
        self.buf.append(np.asarray(frame, dtype=np.float32))
        self.k += 1
        if len(self.buf) < self.T or self.k % self.stride:
            return None
        prob = self.clf.predict(np.stack(self.buf))
        self.n_infer += 1
        self.ema = self.alpha * prob + (1 - self.alpha) * self.ema
        if self.ema[2] >= self.th_fall:
            self.hits += 1
        else:
            self.hits = max(0, self.hits - 1)
        if self.hits >= self.confirm_n:
            return ('낙상 확정', self.ema.copy())
        if self.ema[2] >= self.th_risk or self.ema[1] >= 0.6:
            return ('위험', self.ema.copy())
        if self.ema[1] >= 0.4:
            return ('주의', self.ema.copy())
        return ('정상', self.ema.copy())


# =============================================================
# 5. 카메라 -- Orbbec SDK 또는 Mock
# =============================================================
class SkeletonSource:
    """25관절 스켈레톤을 프레임 단위로 내보내는 소스"""
    def __init__(self, mock=False):
        self.mock = mock
        self.pipeline = None
        if not mock:
            if not self._init_orbbec():
                print("[카메라] Orbbec 초기화 실패 -> Mock 으로 전환")
                self.mock = True

    def _init_orbbec(self):
        try:
            from pyorbbecsdk import Pipeline, Config, OBSensorType
            self.pipeline = Pipeline()
            cfg = Config()
            prof = self.pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
            cfg.enable_stream(prof.get_default_video_stream_profile())
            self.pipeline.start(cfg)
            print("[카메라] Orbbec Femto W 시작")
            return True
        except Exception as e:
            print(f"[카메라] {e}")
            return False

    def start(self):
        if self.mock:
            self._t0 = time.time()

    def read(self):
        """
        (25,3) 좌표 반환. 실패 시 None.

        실제 배포 시 이 부분에 스켈레톤 추출을 넣습니다.
        Orbbec SDK 는 뎁스만 주므로 아래 중 하나가 필요합니다.
          - Orbbec Body Tracking SDK (있으면 25관절 직접 제공)
          - 뎁스 + 포즈 추정 모델로 25관절 산출
        """
        if self.mock:
            return self._mock_frame()
        try:
            frames = self.pipeline.wait_for_frames(100)
            if frames is None:
                return None
            # TODO: 뎁스 프레임 -> 25관절 변환
            return None
        except Exception:
            return None

    def _mock_frame(self):
        """서 있다가 넘어지는 동작을 모사한 가상 스켈레톤"""
        t = time.time() - self._t0
        base = np.array([
            [0.00, 0.90, 2.00],[0.00, 1.15, 2.00],[0.00, 1.38, 1.96],
            [0.00, 1.65, 1.96],[-0.20,1.38, 1.96],[-0.35,1.10, 1.92],
            [-0.40,0.85, 1.92],[-0.42,0.78, 1.92],[0.20, 1.38, 1.96],
            [0.35, 1.10, 1.92],[0.40, 0.85, 1.92],[0.42, 0.78, 1.92],
            [-0.10,0.90, 2.00],[-0.10,0.50, 2.00],[-0.10,0.10, 2.00],
            [-0.11,0.05, 1.90],[0.10, 0.90, 2.00],[0.10, 0.50, 2.00],
            [0.10, 0.10, 2.00],[0.11, 0.05, 1.90],[0.00, 1.38, 1.96],
            [-0.44,0.72, 1.92],[-0.41,0.73, 1.90],[0.44, 0.72, 1.92],
            [0.41, 0.73, 1.90],
        ], dtype=np.float32)
        prog = min(max((t - 2.0) / 0.8, 0.0), 1.0)      # 2초 후 0.8초간 낙하
        ang = prog * math.radians(85)
        c, s = math.cos(ang), math.sin(ang)
        R = np.array([[1,0,0],[0,c,-s],[0,s,c]], dtype=np.float32)
        pivot = base[0].copy()
        pose = (base - pivot) @ R.T + pivot
        pose[:, 1] -= prog * 1.45
        pose += np.random.normal(0, 0.008, pose.shape).astype(np.float32)
        return pose

    def stop(self):
        if self.pipeline:
            try:
                self.pipeline.stop()
            except Exception:
                pass


# =============================================================
# 6. 알람 송출
# =============================================================
class AlarmPublisher:
    def __init__(self, broker=None, port=1883, topic='ymas/fall'):
        self.client = None
        self.topic = topic
        if broker:
            try:
                import paho.mqtt.client as mqtt
                self.client = mqtt.Client()
                self.client.connect(broker, port, 60)
                self.client.loop_start()
                print(f"[알람] MQTT 연결: {broker}:{port}")
            except Exception as e:
                print(f"[알람] MQTT 실패 -> 콘솔 출력만: {e}")

    def publish(self, payload):
        msg = json.dumps(payload, ensure_ascii=False)
        print("\n" + "=" * 62)
        print(" 간호사 스테이션 알람")
        print(f"   {msg}")
        print("=" * 62 + "\n")
        if self.client:
            self.client.publish(self.topic, msg, qos=1)


# =============================================================
# 7. Tier 1 수신 (기존 receiver 와 동일 포맷)
# =============================================================
class Reading:
    __slots__ = ('ms','state','total','cog_x','cog_y','edge','vel',
                 'd_edge','d_w','tte','reason','kg','recv_time')
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.recv_time = time.time()


def parse_line(line):
    line = line.strip()
    if not line.startswith('YMAS,'):
        return None
    p = line.split(',')
    if len(p) != 16:
        return None
    try:
        return Reading(ms=int(p[1]), state=p[2], total=float(p[3]),
                       cog_x=float(p[4]), cog_y=float(p[5]),
                       edge=float(p[6]), vel=float(p[7]),
                       d_edge=float(p[8]), d_w=float(p[9]),
                       tte=float(p[10]), reason=p[11],
                       kg=[float(p[12]),float(p[13]),
                           float(p[14]),float(p[15])])
    except ValueError:
        return None


class Tier1Controller:
    def __init__(self, wake_event, log_path=None, cooldown_s=8.0):
        self.wake_event = wake_event
        self.cooldown_s = cooldown_s
        self.last_wake = 0.0
        self.history = deque(maxlen=800)
        self.last_reading = None
        self.log_file = self.log_writer = None
        if log_path:
            new = not os.path.exists(log_path)
            self.log_file = open(log_path, 'a', newline='')
            self.log_writer = csv.writer(self.log_file)
            if new:
                self.log_writer.writerow(
                    ['ts','esp_ms','state','total','cog_x','cog_y','edge',
                     'vel','d_edge','d_w','tte','reason','FL','FR','RL','RR'])

    def feed(self, r):
        self.history.append(r)
        self.last_reading = r
        if self.log_writer:
            self.log_writer.writerow([
                datetime.now().isoformat(timespec='milliseconds'),
                r.ms, r.state, f"{r.total:.2f}", f"{r.cog_x:.1f}",
                f"{r.cog_y:.1f}", f"{r.edge:.3f}", f"{r.vel:.1f}",
                f"{r.d_edge:.3f}", f"{r.d_w:.1f}", f"{r.tte:.3f}",
                r.reason, *[f"{v:.2f}" for v in r.kg]])
            self.log_file.flush()

        now = time.time()
        if r.state == 'ALERT' and (now - self.last_wake) > self.cooldown_s:
            self.last_wake = now
            print(f"\n[Tier 1] 인터럽트  W={r.total:.1f}kg  "
                  f"edge={r.edge:.3f}  dW={r.d_w:+.1f}kg/s  ({r.reason})")
            self.wake_event.set()
            return True
        return False

    def close(self):
        if self.log_file:
            self.log_file.close()


# =============================================================
# 8. 비전 스레드 -- 여기가 Tier 1 과 Tier 2 를 잇는 지점
# =============================================================
def vision_worker(wake_event, stop_event, ctrl, runtime, cam, alarm,
                  timeout_s=12.0):
    """
    평소 wait() 로 대기하여 카메라와 GPU 를 쉬게 합니다.
    Tier 1 인터럽트가 오면 기상해 최대 timeout_s 동안 관찰합니다.
    """
    while not stop_event.is_set():
        if not wake_event.wait(timeout=0.5):
            continue
        wake_event.clear()

        r = ctrl.last_reading
        print("[Tier 2] 기상 - 카메라 및 ST-GCN 가동")
        if r:
            print(f"         하중 상태 W={r.total:.1f}kg  edge={r.edge:.3f}")

        runtime.reset()
        cam.start()
        t0 = time.time()
        verdict = None
        frames = 0

        while not stop_event.is_set() and (time.time() - t0) < timeout_s:
            frame = cam.read()
            if frame is None:
                time.sleep(0.005)
                continue
            frames += 1
            out = runtime.push(frame)
            if out is None:
                continue
            state, prob = out
            if state == '낙상 확정':
                verdict = (state, prob)
                break
            time.sleep(1.0 / FPS)

        latency = time.time() - t0
        if verdict:
            state, prob = verdict
            alarm.publish({
                'event': 'FALL_CONFIRMED',
                'time': datetime.now().isoformat(timespec='seconds'),
                'tier1': {
                    'total_kg': round(r.total, 2) if r else None,
                    'edge': round(r.edge, 3) if r else None,
                    'reason': r.reason if r else None,
                },
                'tier2': {
                    'prob_fall': round(float(prob[2]), 3),
                    'prob_risk': round(float(prob[1]), 3),
                    'frames': frames,
                    'inferences': runtime.n_infer,
                },
                'latency_s': round(latency, 2),
            })
        else:
            print(f"[Tier 2] 낙상 아님으로 판정 "
                  f"(프레임 {frames}, 추론 {runtime.n_infer}회, "
                  f"낙상확률 {runtime.ema[2]:.2f})")

        print("[Tier 2] 대기 모드 복귀\n")


# =============================================================
# 9. 입력 소스
# =============================================================
def source_udp(port, on_line, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', port))
    sock.settimeout(1.0)
    print(f"[Tier 1] UDP 수신 대기 0.0.0.0:{port}")
    while not stop_event.is_set():
        try:
            data, _ = sock.recvfrom(512)
            on_line(data.decode('utf-8', 'ignore'))
        except socket.timeout:
            continue
    sock.close()


def source_serial(port, baud, on_line, stop_event):
    import serial
    print(f"[Tier 1] 시리얼 {port} @ {baud}")
    with serial.Serial(port, baud, timeout=1.0) as ser:
        while not stop_event.is_set():
            raw = ser.readline().decode('utf-8', 'ignore')
            if raw:
                on_line(raw)


def source_sim(on_line, stop_event, hz=80.0):
    """Tier 1 하중 시뮬레이터 (낙상 시나리오)"""
    PITCH, GAP, W0 = 1700.0, 560.0, 68.0
    dt = 1.0 / hz
    t0 = time.time()
    hist = deque(maxlen=4)
    prev = [0.0, 0.0, time.time()]
    print(f"[Tier 1] 시뮬레이터 {hz:.0f}Hz  4초 안정 -> 6초 낙하")
    while not stop_event.is_set():
        t = time.time() - t0
        cy, w = 0.50, W0
        if t < 4.0:
            pass
        elif t < 6.0:
            cy = 0.50 + 0.18 * (t - 4.0) / 2.0
        elif t < 6.6:
            u = (t - 6.0) / 0.6
            cy = 0.68 + 0.30 * u * u
            w = W0 * (1.0 - 0.75 * u * u)
        else:
            cy, w = 0.98, W0 * 0.20
        cy += np.random.normal(0, 0.006)
        cy = min(max(cy, 0.02), 0.98)
        w = max(w + np.random.normal(0, 0.15), 0.5)

        rear = w * 0.5; front = w - rear
        fr = front * cy; fl = front - fr
        rr = rear * cy;  rl = rear - rr
        cog_x = PITCH * (rl + rr) / w
        cog_y = GAP * (fr + rr) / w
        edge = abs(cog_y / GAP - 0.5) * 2.0

        hist.append((edge, w, time.time()))
        d_edge = d_w = 0.0
        if len(hist) >= 2:
            e0, w0_, t_0 = hist[0]; e1, w1_, t_1 = hist[-1]
            ddt = max(t_1 - t_0, 1e-4)
            d_edge = (e1 - e0) / ddt; d_w = (w1_ - w0_) / ddt
        pdt = max(time.time() - prev[2], 1e-4)
        vel = math.hypot(cog_x - prev[0], cog_y - prev[1]) / pdt
        prev[:] = [cog_x, cog_y, time.time()]

        tte = (0.62 - edge) / d_edge if (d_edge > 0.05 and edge < 0.62) else -1.0
        reason = '-'
        if w < 15: st = 'EMPTY'
        elif d_w <= -45.0 and edge >= 0.30:
            st, reason = 'ALERT', 'FASTPATH_WEIGHT_DROP'
        elif edge >= 0.42 and d_edge >= 1.2 and 0 < tte <= 0.25:
            st, reason = 'ALERT', 'PREDICTIVE_EDGE_RATE'
        elif edge >= 0.62 and (vel >= 250 or (W0-w)/W0 >= 0.25):
            st, reason = 'ALERT', 'STANDARD_EDGE'
        elif edge >= 0.62: st = 'DANGER'
        elif edge >= 0.40: st = 'CAUTION'
        else: st = 'NORMAL'

        on_line(f"YMAS,{int(t*1000)},{st},{w:.2f},{cog_x:.1f},{cog_y:.1f},"
                f"{edge:.3f},{vel:.1f},{d_edge:.3f},{d_w:.1f},{tte:.3f},"
                f"{reason},{fl:.2f},{fr:.2f},{rl:.2f},{rr:.2f}")
        time.sleep(dt)


# =============================================================
def main():
    ap = argparse.ArgumentParser(description='Y-mas 통합 실행')
    ap.add_argument('--udp', type=int, help='Tier 1 UDP 포트')
    ap.add_argument('--port', help='Tier 1 시리얼 포트')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--sim', action='store_true', help='Tier 1 시뮬레이터')
    ap.add_argument('--onnx', help='ymas_v13.onnx 경로')
    ap.add_argument('--trt',  help='ymas_v13.trt 경로 (우선 사용)')
    ap.add_argument('--ckpt', help='ymas_v13_best.pt (pmean/pstd 추출용)')
    ap.add_argument('--mock-camera', action='store_true')
    ap.add_argument('--mqtt', help='MQTT 브로커 주소')
    ap.add_argument('--log', default='ymas_integrated.csv')
    args = ap.parse_args()

    # pmean/pstd 는 학습 시 저장된 값을 써야 정확합니다
    pmean = pstd = None
    if args.ckpt and os.path.exists(args.ckpt):
        try:
            import torch
            ck = torch.load(args.ckpt, map_location='cpu', weights_only=False)
            pmean = np.asarray(ck['pmean'], np.float32)
            pstd  = np.asarray(ck['pstd'],  np.float32)
            print(f"[설정] 체크포인트에서 pmean/pstd 로드")
        except Exception as e:
            print(f"[설정] 체크포인트 로드 실패: {e}")

    clf     = FallClassifier(args.onnx, args.trt, pmean, pstd)
    runtime = YmasRuntime(clf)
    cam     = SkeletonSource(mock=args.mock_camera)
    alarm   = AlarmPublisher(args.mqtt)

    wake_event = threading.Event()
    stop_event = threading.Event()
    ctrl = Tier1Controller(wake_event, log_path=args.log)

    vt = threading.Thread(target=vision_worker,
                          args=(wake_event, stop_event, ctrl,
                                runtime, cam, alarm),
                          daemon=True)
    vt.start()

    cnt = [0]
    def on_line(line):
        r = parse_line(line)
        if r is None:
            return
        ctrl.feed(r)
        cnt[0] += 1
        if r.state in ('DANGER', 'ALERT') or cnt[0] % 80 == 0:
            print(f"[Tier 1] {r.state:7s} W={r.total:6.2f}kg "
                  f"edge={r.edge:.3f} dW={r.d_w:+7.1f}kg/s")

    print("\n=== Y-mas 통합 시스템 시작 ===")
    print(f"  분류기 : {clf.backend}")
    print(f"  카메라 : {'mock' if cam.mock else 'Orbbec'}")
    print(f"  임계값 : th_fall={TH_FALL}  confirm={CONFIRM_N}\n")

    try:
        if args.sim:
            source_sim(on_line, stop_event)
        elif args.udp:
            source_udp(args.udp, on_line, stop_event)
        elif args.port:
            source_serial(args.port, args.baud, on_line, stop_event)
        else:
            ap.print_help()
            print("\n입력 소스를 지정하세요: --udp / --port / --sim")
            return
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        stop_event.set(); wake_event.set()
        vt.join(timeout=3.0)
        cam.stop(); ctrl.close()


if __name__ == '__main__':
    main()
