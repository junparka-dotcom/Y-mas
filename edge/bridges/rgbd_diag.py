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
print("연결됨\n")

for i in range(8):
    header = recv_exact(sock, HEADER_SIZE)
    cw, ch, csize, dw, dh, dsize, ts, fx, fy, cx, cy, depth_scale = struct.unpack(HEADER_FMT, header)
    color_bytes = recv_exact(sock, csize)
    depth_bytes = recv_exact(sock, dsize)
    color_img = cv2.imdecode(np.frombuffer(color_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    depth_arr = np.frombuffer(depth_bytes, dtype=np.uint16).reshape((dh, dw))

    nonzero_frac = np.count_nonzero(depth_arr) / depth_arr.size
    print(f"[{i}] color={cw}x{ch} depth={dw}x{dh} nonzero_frac={nonzero_frac:.2%}")

    if color_img is None:
        continue
    blob, scale = yolo_preprocess(color_img)
    output = session.run(None, {input_name: blob})[0]
    preds = output[0].T
    dets = preds[preds[:, 4] > 0.5]
    if len(dets) == 0:
        continue
    best = dets[np.argmax(dets[:, 4])]
    kpts_2d = best[5:].reshape(17, 3)
    kpts_2d[:, :2] /= scale

    u, v = kpts_2d[0, 0], kpts_2d[0, 1]  # nose
    ui, vi = int(round(u)), int(round(v))
    ui = np.clip(ui, 0, dw-1); vi = np.clip(vi, 0, dh-1)
    raw = depth_arr[vi, ui]
    patch5 = depth_arr[max(0,vi-2):vi+3, max(0,ui-2):ui+3]
    nz5 = patch5[patch5 > 0]
    print(f"     conf={best[4]:.2f}  nose_pixel=({ui},{vi})  raw_depth={raw}  "
          f"5x5_nonzero={len(nz5)}/{patch5.size}  5x5_mean={nz5.mean() if len(nz5)>0 else 0:.1f}")

sock.close()
