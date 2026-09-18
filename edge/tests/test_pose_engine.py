import tensorrt as trt
import numpy as np
import cv2

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)

def load_engine(path):
    with open(path, "rb") as f, trt.Runtime(TRT_LOGGER) as runtime:
        return runtime.deserialize_cuda_engine(f.read())

def preprocess(img_path, size=640):
    img = cv2.imread(img_path)
    h0, w0 = img.shape[:2]
    scale = size / max(h0, w0)
    nh, nw = int(h0 * scale), int(w0 * scale)
    resized = cv2.resize(img, (nw, nh))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(blob[None]), scale, img

engine = load_engine("/home/y-mas/yolo11n-pose_fp16.engine")
context = engine.create_execution_context()

import pycuda.driver as cuda
import pycuda.autoinit

input_blob, scale, orig_img = preprocess("/home/y-mas/test_person.jpg")

d_input = cuda.mem_alloc(input_blob.nbytes)
output_shape = (1, 56, 8400)
output = np.empty(output_shape, dtype=np.float32)
d_output = cuda.mem_alloc(output.nbytes)

context.set_tensor_address("images", int(d_input))
context.set_tensor_address("output0", int(d_output))

stream = cuda.Stream()
cuda.memcpy_htod_async(d_input, input_blob, stream)
context.execute_async_v3(stream_handle=stream.handle)
cuda.memcpy_dtoh_async(output, d_output, stream)
stream.synchronize()

preds = output[0].T  # (8400, 56)
conf_mask = preds[:, 4] > 0.5
detections = preds[conf_mask]
print(f"검출된 사람 수 (conf>0.5): {len(detections)}")

if len(detections) > 0:
    best = detections[np.argmax(detections[:, 4])]
    print(f"최고 확신도 검출 -- confidence: {best[4]:.3f}")
    kpts = best[5:].reshape(17, 3)
    names = ["nose","l_eye","r_eye","l_ear","r_ear","l_shoulder","r_shoulder",
              "l_elbow","r_elbow","l_wrist","r_wrist","l_hip","r_hip",
              "l_knee","r_knee","l_ankle","r_ankle"]
    for name, (x, y, c) in zip(names, kpts):
        print(f"  {name:12s}: x={x/scale:7.1f} y={y/scale:7.1f} conf={c:.2f}")
else:
    print("검출 실패 -- 전처리나 임계값 문제일 수 있음")
