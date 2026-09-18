from pyorbbecsdk import Pipeline, Config, OBSensorType

pipeline = Pipeline()
pipeline.start(None)

import time
time.sleep(1)

for i in range(10):
    frames = pipeline.wait_for_frames(200)
    if frames is None:
        print(f"[{i}] frames None")
        continue
    depth = frames.get_depth_frame()
    if depth is None:
        print(f"[{i}] depth None")
        continue
    data = depth.get_data()
    print(f"[{i}] len(data)={len(data)}  width={depth.get_width()}  height={depth.get_height()}")

pipeline.stop()
