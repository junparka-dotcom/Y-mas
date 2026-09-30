"""
TensorRT 엔진 추론 헬퍼 (Y-mas Tier 2, AGX Orin).

validate_engine.py 에서 검증된 cuda-python(from cuda import cudart) 바인딩 방식을
재사용해, 실시간 파이프라인에서 YOLO11n-pose / ST-GCN 엔진을 GPU로 추론한다.

설계 원칙
- onnxruntime 세션과 "동일한 호출 규약"을 흉내낸다: run(feeds: dict) -> list[np.ndarray]
  → ymas_realtime_ir.py 의 기존 추론 호출부를 최소 수정으로 교체 가능.
- 입력 dict 의 key 는 엔진의 입력 텐서 이름과 일치해야 한다.
- 출력은 엔진의 출력 텐서 순서대로 np.ndarray 리스트로 반환.
- 고정 shape 엔진 기준(우리 두 엔진 모두 고정). 안전을 위해 매 호출 set_input_shape 한다.
"""

import numpy as np
import tensorrt as trt
from cuda import cudart


def _check(err):
    if isinstance(err, tuple):
        err = err[0]
    if err != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"CUDA error: {err}")


class TRTModel:
    """단일 TensorRT 엔진 래퍼. onnxruntime.InferenceSession.run 과 유사한 인터페이스."""

    def __init__(self, engine_path, logger=None):
        self.logger = logger or trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f, trt.Runtime(self.logger) as rt:
            self.engine = rt.deserialize_cuda_engine(f.read())
        if self.engine is None:
            raise RuntimeError(f"엔진 로드 실패: {engine_path}")
        self.context = self.engine.create_execution_context()

        names = [self.engine.get_tensor_name(i)
                 for i in range(self.engine.num_io_tensors)]
        self.input_names = [n for n in names
                            if self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        self.output_names = [n for n in names
                             if self.engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]

        err, self.stream = cudart.cudaStreamCreate()
        _check(err)
        # 디바이스 버퍼 캐시 (텐서명 -> (ptr, nbytes))
        self._dev = {}

    def _dev_buf(self, name, nbytes):
        ptr, cur = self._dev.get(name, (None, 0))
        if ptr is None or cur < nbytes:
            if ptr is not None:
                cudart.cudaFree(ptr)
            err, ptr = cudart.cudaMalloc(nbytes)
            _check(err)
            self._dev[name] = (ptr, nbytes)
        return self._dev[name][0]

    def run(self, feeds):
        """feeds: {input_name: np.ndarray(float32)} -> [np.ndarray, ...] (output 순서)."""
        # 입력 업로드
        for name in self.input_names:
            arr = np.ascontiguousarray(feeds[name].astype(np.float32))
            self.context.set_input_shape(name, arr.shape)
            dptr = self._dev_buf(name, arr.nbytes)
            _check(cudart.cudaMemcpyAsync(
                dptr, arr.ctypes.data, arr.nbytes,
                cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, self.stream))
            self.context.set_tensor_address(name, dptr)

        # 출력 버퍼 준비
        outs = {}
        for name in self.output_names:
            shape = self.context.get_tensor_shape(name)
            host = np.empty(tuple(shape), dtype=np.float32)
            dptr = self._dev_buf(name, host.nbytes)
            self.context.set_tensor_address(name, dptr)
            outs[name] = (host, dptr)

        # 실행
        self.context.execute_async_v3(stream_handle=self.stream)

        # 출력 다운로드
        for name, (host, dptr) in outs.items():
            _check(cudart.cudaMemcpyAsync(
                host.ctypes.data, dptr, host.nbytes,
                cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, self.stream))
        _check(cudart.cudaStreamSynchronize(self.stream))

        return [outs[n][0] for n in self.output_names]

    def __del__(self):
        try:
            for ptr, _ in self._dev.values():
                cudart.cudaFree(ptr)
            cudart.cudaStreamDestroy(self.stream)
        except Exception:
            pass
