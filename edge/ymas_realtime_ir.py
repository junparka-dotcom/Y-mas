import socket
import struct
import time
import json
import sys
import numpy as np
import cv2
import onnxruntime as ort
from collections import deque

# ============================================================
# 학습 코드(ymas_v17.ipynb CELL 2/4/7)와 동일한 상수
# ============================================================
J_SPINE_BASE=0; J_SPINE_MID=1; J_NECK=2; J_HEAD=3
J_SHL=4; J_SHR=8; J_SPINE_SHOULDER=20

NTU_BONES = [
    (1,2),(2,21),(3,21),(4,3),(5,21),(6,5),(7,6),(8,7),
    (9,21),(10,9),(11,10),(12,11),(13,1),(14,13),(15,14),
    (16,15),(17,1),(18,17),(19,18),(20,19),(21,2),
    (22,23),(23,8),(24,25),(25,12),
]
bone_pairs = [(a-1, b-1) for a, b in NTU_BONES]

COCO_SKELETON = [
    (0,1),(0,2),(1,3),(2,4),(0,5),(0,6),(5,7),(7,9),(6,8),(8,10),
    (5,6),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)
]

T = 64
FPS = 30.0
YOLO_SIZE = 320
YOLO_EVERY_N = 2
YOLO_CONF = 0.5

SMOOTH_ALPHA = 0.4
KPT_CONF_MIN = 0.3
MIN_EXIT_DURATION = 0.3
EXIT_ALERT_HOLD = 2.0
EXIT_TILT_THRESHOLD = 45.0

RECONNECT_WAIT = 3.0        # 소켓 재연결 실패 시 대기 시간
LOG_PATH = "/home/y-mas/logs/realtime.log"

PMEAN = np.array([3.3124001026153564, 3.160599946975708, 0.39250001311302185,
                  1.12909996509552, 33.05630111694336, 20.906299591064453,
                  0.4187999963760376, 6.196100234985352, 2.4739999771118164,
                  0.11580000072717667], dtype=np.float32)
PSTD = np.array([7.803500175476074, 5.543000221252441, 0.5598000288009644,
                 0.574400007724762, 27.678300857543945, 22.789499282836914,
                 0.5997999906539919, 9.345000267028809, 0.6205000281333923,
                 0.15379999577999115], dtype=np.float32)

# 배포 임계값. 현재 값 0.75 는 v17 체크포인트 기준.
# [!] v18(ymas_v18_aug) 체크포인트를 배포할 때는 0.78 로 올릴 것.
#     근거: configs/v18_aug.yaml (deploy_th=0.78). v18 은 오경보를 경계
#     구간으로 밀어내 th 0.78 에서 NTU 낙상 recall 유지하며 오경보 -15%.
#     PMEAN/PSTD 도 해당 체크포인트 값으로 함께 맞춰야 한다 (불변식).
TH_FALL = 0.75
CLASS_NAMES = ['Normal', 'Risk', 'Fall']

import os
os.makedirs("/home/y-mas/logs", exist_ok=True)
_log_file = open(LOG_PATH, "a")

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    _log_file.write(line + "\n")
    _log_file.flush()


try:
    with open("/home/y-mas/bed_region.json") as f:
        BED = json.load(f)
    BED_POLY = np.array(BED["polygon_px"], dtype=np.int32)
    log(f"침대(구역) 영역 로드됨: {BED['polygon_px']}")
except FileNotFoundError:
    BED_POLY = None
    log("경고: bed_region.json 없음 -- 영역 판정 없이 전체 화면에서 동작")


def normalize_seq(seq):
    seq = seq.copy(); origin = seq[0, J_SPINE_BASE].copy(); seq -= origin
    sh = seq[0, J_SHR] - seq[0, J_SHL]; theta = np.arctan2(sh[2], sh[0])
    c, s = np.cos(-theta), np.sin(-theta)
    R = np.array([[c,0,-s],[0,1,0],[s,0,c]], dtype=np.float32)
    seq = seq @ R.T
    torso = np.linalg.norm(seq[0, J_SPINE_SHOULDER] - seq[0, J_SPINE_BASE])
    if torso < 1e-3: torso = 1.0
    return (seq / torso).astype(np.float32), float(torso)


def posture_features(seq):
    tail = seq[-5:] if len(seq) >= 5 else seq
    all_y = tail[:, :, 1]
    spine_span = float(all_y.max() - all_y.min())
    shoulder_asym = float(np.abs(tail[:, J_SHR, 1] - tail[:, J_SHL, 1]).mean())
    return spine_span, shoulder_asym


def physics_features(seq, fps):
    dt = 1.0 / fps
    hy = seq[:, J_HEAD, 1]; sy = seq[:, J_SPINE_BASE, 1]
    vh = -np.diff(hy) / dt; vs = -np.diff(sy) / dt
    body = seq[:, J_HEAD] - seq[:, J_SPINE_BASE]
    n = np.linalg.norm(body, axis=1) + 1e-6
    tilt = np.degrees(np.arccos(np.clip(body[:, 1] / n, -1.0, 1.0)))
    horiz = np.linalg.norm(seq[:, J_SPINE_BASE, [0, 2]], axis=1)
    motion = np.linalg.norm(np.diff(seq, axis=0), axis=2).mean(axis=1) / dt
    span, asym = posture_features(seq)
    return np.array([vh.max(), vs.max(), hy[0]-hy.min(), hy[-5:].mean(),
                     tilt.max(), tilt[-5:].mean(), horiz.max(), motion.max(),
                     span, asym], dtype=np.float32)


def resample(seq, T_out):
    idx = np.linspace(0, len(seq)-1, T_out)
    lo = np.floor(idx).astype(int); hi = np.ceil(idx).astype(int)
    w = (idx - lo)[:, None, None].astype(np.float32)
    return (seq[lo]*(1-w) + seq[hi]*w).astype(np.float32)


def to_streams(x):
    joint = x.copy(); bone = np.zeros_like(x)
    for a, b in bone_pairs: bone[:, a] = x[:, a] - x[:, b]
    vel = np.zeros_like(x); vel[1:] = x[1:] - x[:-1]
    return np.concatenate([joint, bone, vel], axis=2).transpose(2, 0, 1)


COCO_NOSE = 0
COCO_LSHOULDER, COCO_RSHOULDER = 5, 6
COCO_LELBOW, COCO_RELBOW = 7, 8
COCO_LWRIST, COCO_RWRIST = 9, 10
COCO_LHIP, COCO_RHIP = 11, 12
COCO_LKNEE, COCO_RKNEE = 13, 14
COCO_LANKLE, COCO_RANKLE = 15, 16

def coco17_to_ntu25(coco_xyz):
    ntu = np.zeros((25, 3), dtype=np.float32)
    shoulder_l, shoulder_r = coco_xyz[COCO_LSHOULDER], coco_xyz[COCO_RSHOULDER]
    hip_l, hip_r = coco_xyz[COCO_LHIP], coco_xyz[COCO_RHIP]
    head = coco_xyz[COCO_NOSE]
    spine_shoulder = (shoulder_l + shoulder_r) / 2
    spine_base = (hip_l + hip_r) / 2
    spine_mid = (spine_shoulder + spine_base) / 2
    neck = (spine_shoulder + head) / 2
    wrist_l, wrist_r = coco_xyz[COCO_LWRIST], coco_xyz[COCO_RWRIST]
    ankle_l, ankle_r = coco_xyz[COCO_LANKLE], coco_xyz[COCO_RANKLE]
    ntu[0]=spine_base;  ntu[1]=spine_mid;  ntu[2]=neck;      ntu[3]=head
    ntu[4]=shoulder_l;  ntu[5]=coco_xyz[COCO_LELBOW]; ntu[6]=wrist_l; ntu[7]=wrist_l
    ntu[8]=shoulder_r;  ntu[9]=coco_xyz[COCO_RELBOW]; ntu[10]=wrist_r; ntu[11]=wrist_r
    ntu[12]=hip_l;      ntu[13]=coco_xyz[COCO_LKNEE]; ntu[14]=ankle_l; ntu[15]=ankle_l
    ntu[16]=hip_r;      ntu[17]=coco_xyz[COCO_RKNEE]; ntu[18]=ankle_r; ntu[19]=ankle_r
    ntu[20]=spine_shoulder
    ntu[21]=wrist_l; ntu[22]=wrist_l; ntu[23]=wrist_r; ntu[24]=wrist_r
    return ntu


CORE_JOINTS_FOR_ZONE = [COCO_LSHOULDER, COCO_RSHOULDER, COCO_LHIP, COCO_RHIP, COCO_NOSE]

def point_in_bed(px, py):
    if BED_POLY is None:
        return True
    return cv2.pointPolygonTest(BED_POLY, (float(px), float(py)), False) >= 0


def person_zone_status(kpts):
    in_count = 0
    out_count = 0
    for j in CORE_JOINTS_FOR_ZONE:
        if kpts[j, 2] < 0.2:
            continue
        if point_in_bed(kpts[j, 0], kpts[j, 1]):
            in_count += 1
        else:
            out_count += 1
    if in_count == 0 and out_count == 0:
        return 'out'
    if out_count == 0:
        return 'in'
    if in_count == 0:
        return 'out'
    return 'partial'


def select_person_in_bed(dets, scale):
    best = None
    best_conf = -1.0
    for det in dets:
        kpts = det[5:].reshape(17, 3).copy()
        kpts[:, :2] /= scale
        status = person_zone_status(kpts)
        if status == 'out':
            continue
        if det[4] > best_conf:
            best_conf = det[4]
            best = kpts
    return best


def update_smoothed_kpts(raw_kpts, smoothed_kpts):
    if smoothed_kpts is None:
        return raw_kpts.copy()
    updated = smoothed_kpts.copy()
    for j in range(17):
        if raw_kpts[j, 2] >= KPT_CONF_MIN:
            updated[j, 0] = SMOOTH_ALPHA * raw_kpts[j, 0] + (1 - SMOOTH_ALPHA) * smoothed_kpts[j, 0]
            updated[j, 1] = SMOOTH_ALPHA * raw_kpts[j, 1] + (1 - SMOOTH_ALPHA) * smoothed_kpts[j, 1]
            updated[j, 2] = raw_kpts[j, 2]
    return updated


SOCKET_PATH = "/tmp/ymas_irdepth.sock"
HEADER_FMT = "<4IQ5f"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def yolo_preprocess(img, size=YOLO_SIZE):
    h0, w0 = img.shape[:2]
    scale = size / max(h0, w0)
    nh, nw = int(h0*scale), int(w0*scale)
    resized = cv2.resize(img, (nw, nh))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(blob[None]), scale


# ============================================================
# 모델 로드 (한 번만)
# ============================================================
sess_opts = ort.SessionOptions()
sess_opts.intra_op_num_threads = 6
sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

pose_session = ort.InferenceSession("/home/y-mas/yolo11n-pose.onnx",
                                     sess_options=sess_opts,
                                     providers=["CPUExecutionProvider"])
pose_input_name = pose_session.get_inputs()[0].name

fall_session = ort.InferenceSession("/home/y-mas/ymas_v17.onnx",
                                     sess_options=sess_opts,
                                     providers=["CPUExecutionProvider"])

WINDOW_SECONDS = 3.0
INFER_INTERVAL = 0.5
MIN_FRAMES = 15


def connect_socket():
    while True:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(SOCKET_PATH)
            log("C++ IR+Depth 브릿지 연결됨.")
            return sock
        except (FileNotFoundError, ConnectionRefusedError) as e:
            log(f"브릿지 연결 실패 ({e}), {RECONNECT_WAIT}초 후 재시도...")
            time.sleep(RECONNECT_WAIT)


def run_session(sock):
    """
    소켓 하나로 계속 프레임을 받아 처리하는 메인 루프.
    사용자가 'q'를 누르면 (True, False) 반환 -> 프로그램 완전 종료.
    소켓 예외가 나면 (False, True) 반환 -> 재연결 후 재시작.
    """
    buffer = deque()
    last_infer_time = 0.0
    last_result_text = "collecting..."
    last_result_color = (200, 200, 200)
    frame_count = 0
    cached_kpts_2d = None
    smoothed_kpts_2d = None

    out_of_bed_since = None
    confirmed_exit = False
    exit_event_time = 0.0

    fps_t0 = time.time()
    fps_counter = 0
    display_fps = 0.0

    while True:
        header = recv_exact(sock, HEADER_SIZE)
        w, h, ir_size, depth_size, ts, fx, fy, cx, cy, depth_scale = struct.unpack(HEADER_FMT, header)
        ir_bytes = recv_exact(sock, ir_size)
        depth_bytes = recv_exact(sock, depth_size)

        ir_arr = np.frombuffer(ir_bytes, dtype=np.uint16).reshape((h, w))
        depth_arr = np.frombuffer(depth_bytes, dtype=np.uint16).reshape((h, w))

        ir_8u = cv2.normalize(ir_arr, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        ir_3ch = cv2.cvtColor(ir_8u, cv2.COLOR_GRAY2BGR)
        display_img = ir_3ch.copy()

        if BED_POLY is not None:
            cv2.polylines(display_img, [BED_POLY], True, (255, 128, 0), 2)

        now = time.time()
        frame_count += 1

        if frame_count % YOLO_EVERY_N == 0:
            blob, scale = yolo_preprocess(ir_3ch)
            output = pose_session.run(None, {pose_input_name: blob})[0]
            preds = output[0].T
            dets = preds[preds[:, 4] > YOLO_CONF]
            raw_kpts_2d = select_person_in_bed(dets, scale)
            if raw_kpts_2d is not None:
                smoothed_kpts_2d = update_smoothed_kpts(raw_kpts_2d, smoothed_kpts_2d)
                cached_kpts_2d = smoothed_kpts_2d
            else:
                smoothed_kpts_2d = None
                cached_kpts_2d = None

        kpts_2d = cached_kpts_2d
        zone_status = 'out' if kpts_2d is None else person_zone_status(kpts_2d)
        in_bed_now = (zone_status == 'in')

        if kpts_2d is not None:
            for (a, b) in COCO_SKELETON:
                if kpts_2d[a, 2] > 0.3 and kpts_2d[b, 2] > 0.3:
                    pa = (int(kpts_2d[a, 0]), int(kpts_2d[a, 1]))
                    pb = (int(kpts_2d[b, 0]), int(kpts_2d[b, 1]))
                    cv2.line(display_img, pa, pb, (0, 255, 0), 2)
            for j in range(17):
                if kpts_2d[j, 2] > 0.3:
                    p = (int(kpts_2d[j, 0]), int(kpts_2d[j, 1]))
                    cv2.circle(display_img, p, 4, (0, 0, 255), -1)

            coco_xyz = np.zeros((17, 3), dtype=np.float32)
            valid = True
            for j in range(17):
                u, v = kpts_2d[j, 0], kpts_2d[j, 1]
                ui = int(np.clip(round(u), 0, w-1)); vi = int(np.clip(round(v), 0, h-1))
                depth_mm = float(depth_arr[vi, ui]) * depth_scale
                if depth_mm <= 0:
                    patch = depth_arr[max(0,vi-2):vi+3, max(0,ui-2):ui+3].astype(np.float32)
                    nz = patch[patch > 0]
                    depth_mm = float(nz.mean()) * depth_scale if len(nz) > 0 else 0.0
                X = (u - cx) * depth_mm / fx / 1000.0
                Y = -(v - cy) * depth_mm / fy / 1000.0
                Z = depth_mm / 1000.0
                coco_xyz[j] = [X, Y, Z]
                if Z <= 0: valid = False

            if valid:
                ntu25 = coco17_to_ntu25(coco_xyz)
                buffer.append((now, ntu25))

        if in_bed_now:
            out_of_bed_since = None
            confirmed_exit = False
        else:
            if out_of_bed_since is None:
                out_of_bed_since = now
            elif not confirmed_exit and (now - out_of_bed_since) >= MIN_EXIT_DURATION:
                confirmed_exit = True
                exit_event_time = now
                log(f"*** 구역 이탈 감지 (확정, {MIN_EXIT_DURATION}s 이상 지속) ***")
        recent_exit = confirmed_exit and (now - exit_event_time) < EXIT_ALERT_HOLD

        while buffer and now - buffer[0][0] > WINDOW_SECONDS:
            buffer.popleft()

        if len(buffer) >= MIN_FRAMES and (now - last_infer_time) >= INFER_INTERVAL:
            last_infer_time = now
            seq_raw = np.stack([f[1] for f in buffer], axis=0)
            seq_resampled = resample(seq_raw, T)
            seq_norm, torso = normalize_seq(seq_resampled)
            phys = physics_features(seq_resampled, FPS)
            phys_norm = (phys - PMEAN) / PSTD
            skel_stream = to_streams(seq_norm)

            skel_input = skel_stream[None].astype(np.float32)
            phys_input = phys_norm[None].astype(np.float32)

            logits = fall_session.run(None, {"skeleton": skel_input, "physics": phys_input})[0][0]
            probs = np.exp(logits) / np.exp(logits).sum()

            pred_class = 2 if probs[2] >= TH_FALL else int(np.argmax(probs[:2]))

            posture_abnormal = phys[5] >= EXIT_TILT_THRESHOLD
            forced_fall = recent_exit and posture_abnormal
            if forced_fall:
                pred_class = 2

            exit_tag = "[EXIT+TILT]" if forced_fall else ("[EXIT,정상자세]" if recent_exit else "")

            last_result_text = (f"{CLASS_NAMES[pred_class]}  N={probs[0]:.2f} "
                                 f"R={probs[1]:.2f} F={probs[2]:.2f}  {exit_tag}")
            last_result_color = (0, 0, 255) if pred_class == 2 else \
                                 (0, 255, 255) if pred_class == 1 else (0, 255, 0)

            tag = "*** FALL ALERT ***" if pred_class == 2 else ""
            log(f"buf={len(buffer):2d} zone={zone_status:7s} tilt_final={phys[5]:5.1f} {exit_tag} "
                f"P(Normal)={probs[0]:.2f} P(Risk)={probs[1]:.2f} P(Fall)={probs[2]:.2f} "
                f"-> {CLASS_NAMES[pred_class]}  {tag}")

        fps_counter += 1
        if now - fps_t0 >= 1.0:
            display_fps = fps_counter / (now - fps_t0)
            fps_counter = 0
            fps_t0 = now

        status_labels = {'in': 'IN-ZONE', 'partial': 'PARTIAL-EXIT', 'out': 'OUT-OF-ZONE/NO PERSON'}
        status = status_labels[zone_status]
        cv2.putText(display_img, f"FPS={display_fps:.1f}  {status}",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
        cv2.putText(display_img, last_result_text, (10, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, last_result_color, 2)

        cv2.imshow("Y-mas Realtime (IR + Zone + Smoothing)", display_img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            return True   # 사용자 정상 종료


# ============================================================
# 최상위 재시작 루프
# ============================================================
log("=== Y-mas 실시간 감지 시작 ('q' 키로 완전 종료) ===")

try:
    while True:
        sock = connect_socket()
        try:
            user_quit = run_session(sock)
            sock.close()
            cv2.destroyAllWindows()
            if user_quit:
                log("=== 사용자 종료 (q 키) -- 프로그램 종료 ===")
                break
        except (ConnectionError, struct.error, OSError) as e:
            log(f"소켓/연결 오류 발생: {e} -- {RECONNECT_WAIT}초 후 재연결")
            try:
                sock.close()
            except Exception:
                pass
            cv2.destroyAllWindows()
            time.sleep(RECONNECT_WAIT)
        except Exception as e:
            log(f"예상치 못한 오류: {e} -- {RECONNECT_WAIT}초 후 재시작")
            try:
                sock.close()
            except Exception:
                pass
            cv2.destroyAllWindows()
            time.sleep(RECONNECT_WAIT)
except KeyboardInterrupt:
    log("=== Ctrl+C로 종료 ===")
    cv2.destroyAllWindows()

_log_file.close()
sys.exit(0)
