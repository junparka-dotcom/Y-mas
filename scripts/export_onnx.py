#!/usr/bin/env python3
"""체크포인트 -> ONNX (opset 18) 내보내기.

사용법:
    python scripts/export_onnx.py --ckpt ymas_v17.pt --out ymas_v17.onnx

ymas_v17.onnx.data (external weight) 가 함께 생성되면 Jetson 으로 반드시
같이 전송할 것. pmean/pstd 도 함께 출력하므로 추론 코드에 반영한다.
"""

import argparse
import os
import sys

# 래포 루트를 모듈 검색 경로에 추가 (scripts/ 에서 직접 실행해도 ymas 임포트 가능)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch

from ymas.config import Config
from ymas.model import YmasNet
from ymas.export import export_onnx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location='cpu', weights_only=False)
    cfg = Config(**{k: v for k, v in ck.get('cfg', {}).items()
                    if k in Config.__dataclass_fields__})
    model = YmasNet(n_phys=cfg.n_phys, dropout=cfg.dropout)
    model.load_state_dict(ck['model'])
    export_onnx(model, cfg, args.out, device='cpu')

    print("ONNX 저장:", args.out)
    print("pmean =", [round(float(v), 4) for v in ck['pmean']])
    print("pstd  =", [round(float(v), 4) for v in ck['pstd']])
    print("\nJetson TensorRT FP16 빌드:")
    print(f"  /usr/src/tensorrt/bin/trtexec --onnx={args.out} \\")
    print(f"    --saveEngine=ymas_v17_fp16.engine --fp16 \\")
    print(f"    --shapes=skeleton:1x9x{cfg.T}x{cfg.V},physics:1x{cfg.n_phys}")


if __name__ == '__main__':
    main()
