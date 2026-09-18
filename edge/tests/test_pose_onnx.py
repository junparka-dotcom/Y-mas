import onnxruntime as ort
import numpy as np
import cv2

def preprocess(img_path, size=640):
    img = cv2.imread(img_path)
    h0, w0 = img.shape[:2]
    scale = size / max(h0, w0)
    nh, nw = int(h0 * scale), int(w0 * scale)
    resized = cv2.resize(img, (nw, nh))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.ascontiguousarray(blob[None]), scale

session = ort.InferenceSession("/home/y-mas/yolo11n-pose.onnx", providers=["CPUExecutionProvider"])
input_name = session.get_inputs()[0].name

input_blob, scale = preprocess("/home/y-mas/test_person.jpg")
output = session.run(None, {input_name: input_blob})[0]

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
    print("검출 실패")

# --- 17 -> 25 관절 매핑 테스트 ---
coco_xyz = np.zeros((17, 3), dtype=np.float32)
coco_xyz[:, :2] = kpts[:, :2] / scale
coco_xyz[:, 2] = 0  # Z는 아직 뎁스 lifting 전

def coco17_to_ntu25(coco_xyz):
    ntu = np.zeros((25, 3), dtype=np.float32)
    shoulder_l, shoulder_r = coco_xyz[5], coco_xyz[6]
    hip_l, hip_r = coco_xyz[11], coco_xyz[12]
    head = coco_xyz[0]
    spine_shoulder = (shoulder_l + shoulder_r) / 2
    spine_base = (hip_l + hip_r) / 2
    spine_mid = (spine_shoulder + spine_base) / 2
    neck = (spine_shoulder + head) / 2
    wrist_l, wrist_r = coco_xyz[9], coco_xyz[10]
    ankle_l, ankle_r = coco_xyz[15], coco_xyz[16]
    ntu[0]=spine_base; ntu[1]=spine_mid; ntu[2]=neck; ntu[3]=head
    ntu[4]=shoulder_l; ntu[5]=coco_xyz[7]; ntu[6]=wrist_l; ntu[7]=wrist_l
    ntu[8]=shoulder_r; ntu[9]=coco_xyz[8]; ntu[10]=wrist_r; ntu[11]=wrist_r
    ntu[12]=hip_l; ntu[13]=coco_xyz[13]; ntu[14]=ankle_l; ntu[15]=ankle_l
    ntu[16]=hip_r; ntu[17]=coco_xyz[14]; ntu[18]=ankle_r; ntu[19]=ankle_r
    ntu[20]=spine_shoulder; ntu[21]=wrist_l; ntu[22]=wrist_l; ntu[23]=wrist_r; ntu[24]=wrist_r
    return ntu

ntu25 = coco17_to_ntu25(coco_xyz)
ntu_names = ["SpineBase","SpineMid","Neck","Head","ShoulderL","ElbowL","WristL","HandL",
             "ShoulderR","ElbowR","WristR","HandR","HipL","KneeL","AnkleL","FootL",
             "HipR","KneeR","AnkleR","FootR","SpineShoulder","HandTipL","ThumbL","HandTipR","ThumbR"]
print("\n=== NTU-25 매핑 결과 ===")
for name, (x, y, z) in zip(ntu_names, ntu25):
    print(f"  {name:14s}: x={x:7.1f} y={y:7.1f}")
