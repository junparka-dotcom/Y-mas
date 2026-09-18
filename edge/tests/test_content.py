from pyorbbecsdk import Pipeline
import numpy as np
import time

pipeline = Pipeline()
pipeline.start(None)
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
    raw = np.frombuffer(bytes(depth.get_data()), dtype=np.uint16)
    print(f"[{i}] len={len(raw)}  min={raw.min()}  max={raw.max()}  mean={raw.mean():.1f}  nonzero_count={np.count_nonzero(raw)}")

pipeline.stop()
