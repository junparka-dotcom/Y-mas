import socket
import struct
import time
import numpy as np
import cv2
import onnxruntime as ort
from collections import deque

# ============================================================
# 학습 코드(ymas_v17.ipynb CELL 2/4/7)와 동일해야 하는 상수
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

T = 64
FPS = 30.0

# CELL 12 ONNX export 시점에 출력된 정확한 정규화 값
PMEAN = np.array([3.3124001026153564, 3.160599946975708, 0.39250001311302185,
                  1.12909996509552, 33.05630111694336, 20.906299591064453,
                  0.4187999963760376, 6.196100234985352, 2.4739999771118164,
                  0.11580000072717667], dtype=np.float32)
PSTD = np.array([7.803500175476074, 5.543000221252441, 0.5598000288009644,
                 0.574400007724762, 27.678300857543945, 22.789499282836914,
                 0.5997999906539917, 9.345000267028809, 0.6205000281333923,
                 0.15379999577999115], dtype=np.float32)

TH_FALL = 0.75  # v17 확정 임계값
CLASS_NAMES = ['Normal', 'Risk', 'Fall']


# ============================================================
# 학습 코드 원본 그대로 (ymas_v17.ipynb CELL 4, CELL 7)
# ============================================================
def normalize_seq(seq):
    """SpineBase 원점 이동 + 어깨선 기준 Y축 회전 + 몸통 길이 정규화"""
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


# ============================================================
# COCO-17 -> NTU-25 매핑 (오늘 검증 완료)
# ============================================================
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


# ============================================================
# 소켓 / 모델 초기화
# ============================================================
SOCKET_PATH = "/tmp/ymas_rgbd.sock"
HEADER_FMT = "<6IQ5f"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf

pose_session = ort.InferenceSession("/home/y-mas/yolo11n-pose.onnx", providers=["CPUExecutionProvider"])
pose_input_name = pose_session.get_inputs()[0].name

fall_session = ort.InferenceSession("/home/y-mas/ymas_v17.onnx", providers=["CPUExecutionProvider"])

def yolo_preprocess(img, size=640):
    h0, w0 = img.shape[:2]
    scale = size / max(h0, w0)
    nh, nw = int(h0*scale), int(w0*scale)
    resized = cv2.resize(img, (nw, nh))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(blob[None]), scale


# ============================================================
# 메인 루프
# ============================================================
WINDOW_SECONDS = 3.0     # 이 시간 동안의 스켈레톤을 버퍼링
INFER_INTERVAL = 0.5     # 이 주기로 판정 실행
MIN_FRAMES = 15          # 최소 이 프레임 이상 모이면 판정 시도

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(SOCKET_PATH)
print("C++ 브릿지 연결됨. 실시간 추론 시작...\n")

buffer = deque()  # (timestamp, ntu25(25,3)) 튜플
last_infer_time = 0.0

while True:
    header = recv_exact(sock, HEADER_SIZE)
    cw, ch, csize, dw, dh, dsize, ts, fx, fy, cx, cy, depth_scale = struct.unpack(HEADER_FMT, header)
    color_bytes = recv_exact(sock, csize)
    depth_bytes = recv_exact(sock, dsize)

    color_img = np.frombuffer(color_bytes, dtype=np.uint8).reshape((ch, cw, 3))
    color_img_bgr = cv2.cvtColor(color_img, cv2.COLOR_RGB2BGR)
    depth_arr = np.frombuffer(depth_bytes, dtype=np.uint16).reshape((dh, dw))

    blob, scale = yolo_preprocess(color_img_bgr)
    output = pose_session.run(None, {pose_input_name: blob})[0]
    preds = output[0].T
    dets = preds[preds[:, 4] > 0.5]
    now = time.time()

    print(f"검출={len(dets)}  버퍼={len(buffer)}")
    if len(dets) > 0:
        best = dets[np.argmax(dets[:, 4])]
        kpts_2d = best[5:].reshape(17, 3)
        kpts_2d[:, :2] /= scale

        coco_xyz = np.zeros((17, 3), dtype=np.float32)
        valid = True
        for j in range(17):
            u, v = kpts_2d[j, 0], kpts_2d[j, 1]
            ui = int(np.clip(round(u), 0, dw-1)); vi = int(np.clip(round(v), 0, dh-1))
            depth_mm = float(depth_arr[vi, ui]) * depth_scale
            if depth_mm <= 0:
                patch = depth_arr[max(0,vi-2):vi+3, max(0,ui-2):ui+3].astype(np.float32)
                nz = patch[patch > 0]
                depth_mm = float(nz.mean()) * depth_scale if len(nz) > 0 else 0.0
            X = (u - cx) * depth_mm / fx / 1000.0
            Y = (v - cy) * depth_mm / fy / 1000.0
            Z = depth_mm / 1000.0
            coco_xyz[j] = [X, Y, Z]
            if Z <= 0: valid = False

        if valid:
            ntu25 = coco17_to_ntu25(coco_xyz)
            buffer.append((now, ntu25))

    while buffer and now - buffer[0][0] > WINDOW_SECONDS:
        buffer.popleft()

    if len(buffer) >= MIN_FRAMES and (now - last_infer_time) >= INFER_INTERVAL:
        last_infer_time = now
        seq_raw = np.stack([f[1] for f in buffer], axis=0)  # (N,25,3)
        seq_resampled = resample(seq_raw, T)                 # (64,25,3)
        seq_norm, torso = normalize_seq(seq_resampled)
        phys = physics_features(seq_resampled, FPS)
        phys_norm = (phys - PMEAN) / PSTD
        skel_stream = to_streams(seq_norm)                   # (9,64,25)

        skel_input = skel_stream[None].astype(np.float32)
        phys_input = phys_norm[None].astype(np.float32)

        logits = fall_session.run(None, {"skeleton": skel_input, "physics": phys_input})[0][0]
        probs = np.exp(logits) / np.exp(logits).sum()

        pred_class = 2 if probs[2] >= TH_FALL else int(np.argmax(probs[:2]))
        tag = "*** FALL ALERT ***" if pred_class == 2 else ""
        print(f"[{time.strftime('%H:%M:%S')}] buf={len(buffer):2d} "
              f"P(Normal)={probs[0]:.2f} P(Risk)={probs[1]:.2f} P(Fall)={probs[2]:.2f} "
              f"-> {CLASS_NAMES[pred_class]}  {tag}")
