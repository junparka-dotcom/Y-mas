from pyorbbecsdk import Pipeline, Config, OBSensorType
import numpy as np
import time

pipeline = Pipeline()
config = Config()

try:
    ir_profile_list = pipeline.get_stream_profile_list(OBSensorType.IR_SENSOR)
    ir_profile = ir_profile_list.get_default_video_stream_profile()
    config.enable_stream(ir_profile)
    print("IR profile:", ir_profile)
except Exception as e:
    print("IR 스트림 활성화 실패:", e)
    exit(1)

pipeline.start(config)
time.sleep(1)

for i in range(10):
    frames = pipeline.wait_for_frames(200)
    if frames is None:
        print(f"[{i}] frames None")
        continue
    ir = frames.get_ir_frame()
    if ir is None:
        print(f"[{i}] ir None")
        continue
    raw = np.frombuffer(bytes(ir.get_data()), dtype=np.uint8)
    print(f"[{i}] len={len(raw)}  min={raw.min()}  max={raw.max()}  mean={raw.mean():.1f}  nonzero={np.count_nonzero(raw)}")

pipeline.stop()
