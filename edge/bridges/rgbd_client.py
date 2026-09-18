import socket
import struct
import numpy as np
import cv2
import onnxruntime as ort

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

NTU_NAMES = ["SpineBase","SpineMid","Neck","Head","ShoulderL","ElbowL","WristL","HandL",
             "ShoulderR","ElbowR","WristR","HandR","HipL","KneeL","AnkleL","FootL",
             "HipR","KneeR","AnkleR","FootR","SpineShoulder","HandTipL","ThumbL","HandTipR","ThumbR"]

session = ort.InferenceSession("/home/y-mas/yolo11n-pose.onnx", providers=["CPUExecutionProvider"])
input_name = session.get_inputs()[0].name

def yolo_preprocess(img, size=640):
    h0, w0 = img.shape[:2]
    scale = size / max(h0, w0)
    nh, nw = int(h0*scale), int(w0*scale)
    resized = cv2.resize(img, (nw, nh))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(blob[None]), scale

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(SOCKET_PATH)
print("C++ 브릿지 연결됨")

for i in range(15):
    header = recv_exact(sock, HEADER_SIZE)
    cw, ch, csize, dw, dh, dsize, ts, fx, fy, cx, cy, depth_scale = struct.unpack(HEADER_FMT, header)

    color_bytes = recv_exact(sock, csize)
    depth_bytes = recv_exact(sock, dsize)

    color_img = cv2.imdecode(np.frombuffer(color_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    depth_arr = np.frombuffer(depth_bytes, dtype=np.uint16).reshape((dh, dw))

    if color_img is None:
        print(f"[{i}] 컬러 디코드 실패 (size={csize})")
        continue

    blob, scale = yolo_preprocess(color_img)
    output = session.run(None, {input_name: blob})[0]
    preds = output[0].T
    dets = preds[preds[:, 4] > 0.5]
    if len(dets) == 0:
        print(f"[{i}] 사람 검출 안됨")
        continue

    best = dets[np.argmax(dets[:, 4])]
    kpts_2d = best[5:].reshape(17, 3)
    kpts_2d[:, :2] /= scale

    coco_xyz = np.zeros((17, 3), dtype=np.float32)
    for j in range(17):
        u, v = kpts_2d[j, 0], kpts_2d[j, 1]
        ui = int(np.clip(round(u), 0, dw - 1))
        vi = int(np.clip(round(v), 0, dh - 1))
        depth_mm = float(depth_arr[vi, ui]) * depth_scale
        if depth_mm <= 0:
            patch = depth_arr[max(0, vi-1):vi+2, max(0, ui-1):ui+2].astype(np.float32)
            nz = patch[patch > 0]
            depth_mm = float(nz.mean()) * depth_scale if len(nz) > 0 else 0.0
        X = (u - cx) * depth_mm / fx / 1000.0
        Y = (v - cy) * depth_mm / fy / 1000.0
        Z = depth_mm / 1000.0
        coco_xyz[j] = [X, Y, Z]

    ntu25 = coco17_to_ntu25(coco_xyz)
    print(f"\n[{i}] conf={best[4]:.2f} -- NTU-25 3D (meters)")
    for name, (x, y, z) in zip(NTU_NAMES, ntu25):
        print(f"  {name:14s}: x={x:6.3f} y={y:6.3f} z={z:6.3f}")

sock.close()
