"""Jetson 배포용 ONNX 내보내기 (opset 18).

노트북 CELL 12 와 동일. 산출물:
  ymas_v17.onnx (+ ymas_v17.onnx.data)  -- external weight, 반드시 함께 전송

Jetson 에서 TensorRT FP16 엔진 빌드:
  /usr/src/tensorrt/bin/trtexec \
    --onnx=ymas_v17.onnx \
    --saveEngine=ymas_v17_fp16.engine \
    --fp16 \
    --shapes=skeleton:1x9x64x25,physics:1x10

[!] 추론 시 pmean/pstd 필요 -- 체크포인트에서 읽어 physics 정규화에 사용.
    학습·추론이 완전히 같은 pmean/pstd 를 써야 한다 (교훈 5).
"""

import torch


def export_onnx(model, cfg, onnx_path, device='cpu'):
    """모델을 ONNX 로 내보낸다. dynamic batch axis."""
    model.eval()
    dummy_x = torch.randn(1, 9, cfg.T, cfg.V, device=device)
    dummy_p = torch.randn(1, cfg.n_phys, device=device)
    torch.onnx.export(
        model, (dummy_x, dummy_p), onnx_path,
        input_names=['skeleton', 'physics'], output_names=['logits'],
        dynamic_axes={'skeleton': {0: 'batch'}, 'physics': {0: 'batch'},
                      'logits': {0: 'batch'}},
        opset_version=18)
    return onnx_path
