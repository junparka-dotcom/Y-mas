"""
Y-mas v13 TensorRT 엔진 검증 스크립트
Colab에서 뽑은 홀드아웃 샘플(validation_samples_v17.npz)을 TensorRT 엔진에 넣고,
PyTorch가 낸 로짓(pytorch_logits)과 비교한다.

사용법:
    python3 validate_engine.py
"""

import numpy as np
import tensorrt as trt
from cuda import cudart

ENGINE_PATH = "ymas_v17_fp16.engine"
NPZ_PATH = "validation_samples_v17.npz"


def check_cuda(err):
    if err != cudart.cudaError_t.cudaSuccess:
        raise RuntimeError(f"CUDA error: {err}")


def load_engine(path):
    logger = trt.Logger(trt.Logger.WARNING)
    with open(path, "rb") as f, trt.Runtime(logger) as runtime:
        engine = runtime.deserialize_cuda_engine(f.read())
    if engine is None:
        raise RuntimeError("엔진 로드 실패 - 파일 경로/버전을 확인하세요")
    return engine


def main():
    print(f"엔진 로드: {ENGINE_PATH}")
    engine = load_engine(ENGINE_PATH)
    context = engine.create_execution_context()

    # 입출력 텐서 이름 확인 (TensorRT 10.x API)
    tensor_names = [engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]
    print("텐서 목록:", tensor_names)

    input_names = [n for n in tensor_names
                   if engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
    output_names = [n for n in tensor_names
                    if engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
    print("입력:", input_names, " 출력:", output_names)

    # 검증 데이터 로드
    data = np.load(NPZ_PATH)
    skeletons = data["skeletons"].astype(np.float32)   # (N, 9, 64, 25)
    physics = data["physics"].astype(np.float32)        # (N, 8)
    labels = data["labels"]
    pytorch_logits = data["pytorch_logits"].astype(np.float32)  # (N, 3)

    N = skeletons.shape[0]
    print(f"\n검증 샘플 수: {N}")

    # 스트림 생성
    err, stream = cudart.cudaStreamCreate()
    check_cuda(err)

    trt_logits_all = []

    for i in range(N):
        skel_i = np.ascontiguousarray(skeletons[i:i+1])   # (1,9,64,25)
        phys_i = np.ascontiguousarray(physics[i:i+1])       # (1,8)

        # 입력 shape 설정 (동적 shape 대응, 고정이라도 안전하게 매번 지정)
        context.set_input_shape("skeleton", skel_i.shape)
        context.set_input_shape("physics", phys_i.shape)

        out_shape = context.get_tensor_shape("logits")
        out_host = np.empty(out_shape, dtype=np.float32)

        # GPU 메모리 할당
        err, skel_dev = cudart.cudaMalloc(skel_i.nbytes)
        check_cuda(err)
        err, phys_dev = cudart.cudaMalloc(phys_i.nbytes)
        check_cuda(err)
        err, out_dev = cudart.cudaMalloc(out_host.nbytes)
        check_cuda(err)

        # Host -> Device 복사
        err, = cudart.cudaMemcpyAsync(
            skel_dev, skel_i.ctypes.data, skel_i.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, stream)
        check_cuda(err)
        err, = cudart.cudaMemcpyAsync(
            phys_dev, phys_i.ctypes.data, phys_i.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyHostToDevice, stream)
        check_cuda(err)

        # 텐서 주소 바인딩
        context.set_tensor_address("skeleton", skel_dev)
        context.set_tensor_address("physics", phys_dev)
        context.set_tensor_address("logits", out_dev)

        # 추론 실행
        context.execute_async_v3(stream_handle=stream)

        # Device -> Host 복사
        err, = cudart.cudaMemcpyAsync(
            out_host.ctypes.data, out_dev, out_host.nbytes,
            cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost, stream)
        check_cuda(err)

        err = cudart.cudaStreamSynchronize(stream)[0]
        check_cuda(err)

        trt_logits_all.append(out_host.copy().reshape(-1))

        cudart.cudaFree(skel_dev)
        cudart.cudaFree(phys_dev)
        cudart.cudaFree(out_dev)

    cudart.cudaStreamDestroy(stream)

    trt_logits_all = np.array(trt_logits_all)  # (N, 3)

    # ---- 비교 ----
    print("\n" + "=" * 70)
    print(f"{'idx':>4} {'label':>6} {'PT_pred':>8} {'TRT_pred':>9} "
          f"{'max|diff|':>10} {'match':>6}")
    print("-" * 70)

    all_match = True
    max_diffs = []
    for i in range(N):
        pt = pytorch_logits[i]
        tr = trt_logits_all[i]
        diff = np.abs(pt - tr)
        max_diff = diff.max()
        max_diffs.append(max_diff)

        pt_pred = int(np.argmax(pt))
        tr_pred = int(np.argmax(tr))
        match = (pt_pred == tr_pred)
        all_match &= match

        print(f"{i:>4} {int(labels[i]):>6} {pt_pred:>8} {tr_pred:>9} "
              f"{max_diff:>10.5f} {'OK' if match else 'MISMATCH':>6}")

    print("=" * 70)
    print(f"평균 max|diff|: {np.mean(max_diffs):.6f}")
    print(f"최대 max|diff|: {np.max(max_diffs):.6f}")
    print(f"예측 클래스 전체 일치: {'YES' if all_match else 'NO'}")

    if np.max(max_diffs) < 0.05 and all_match:
        print("\n 검증 통과: FP16 엔진이 PyTorch와 사실상 동일하게 동작합니다.")
    elif all_match:
        print("\n  예측 클래스는 일치하지만 로짓 차이가 다소 있습니다 "
              "(FP16 반올림 오차로 정상적인 범위일 수 있습니다).")
    else:
        print("\n 일부 샘플에서 예측 클래스가 불일치합니다. "
              "th=0.7 임계값 근처 샘플이면 재조정이 필요할 수 있습니다.")


if __name__ == "__main__":
    main()
