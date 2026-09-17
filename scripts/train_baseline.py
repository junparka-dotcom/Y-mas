#!/usr/bin/env python3
"""v17 재현 베이스라인 학습 진입점.

사용법 (Colab / 로컬 GPU):
    python scripts/train_baseline.py \
        --ntu   /path/ntu_ymas_v15_T64.npz \
        --etri  /path/etri_ymas_v15_T64.npz \
        --out   /path/ckpt/ymas_v17_repro.pt

이 스크립트는 검증된 v17 파이프라인을 그대로 재현한다. 목표는 인수인계
문서의 숫자(Holdout NTU recall ~0.976, ETRI ~0.988)를 재현해 이후 개선
실험의 비교 기준(baseline)을 확립하는 것이다.
"""

import argparse
import os
import sys

# 래포 루트를 모듈 검색 경로에 추가 (scripts/ 에서 직접 실행해도 ymas 임포트 가능)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import torch

from ymas.config import Config, load_config
from ymas.dataset import load_caches, build_datasets, build_loaders, summarize_split
from ymas.model import YmasNet, count_parameters
from ymas.train import train, set_seed
from ymas.evaluate import (
    evaluate, sweep_thresholds, select_threshold, holdout_report, diagnose_features,
)
from ymas.skeleton import CLASS_NAMES


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ntu', required=True, help='ntu_ymas_v15_T64.npz 경로')
    ap.add_argument('--etri', required=True, help='etri_ymas_v15_T64.npz 경로')
    ap.add_argument('--out', required=True, help='체크포인트 저장 경로 (.pt)')
    ap.add_argument('--config', default=None, help='실험 config YAML (없으면 v17 기본값)')
    ap.add_argument('--epochs', type=int, default=None, help='epochs 오버라이드 (스모크용)')
    ap.add_argument('--num-workers', type=int, default=2)
    return ap.parse_args()


def main():
    args = parse_args()
    cfg = load_config(args.config)
    if args.epochs is not None:
        cfg.epochs = args.epochs
    set_seed(cfg.seed)
    print("config:", args.config or "(v17 기본값)")
    print(f"  aug_rot_y={cfg.aug_rot_y} aug_rot_xz={cfg.aug_rot_xz} "
          f"joint_dropout_p={cfg.joint_dropout_p} joint_dropout_frac={cfg.joint_dropout_frac}")

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print("device:", device)
    if device == 'cuda':
        print("GPU   :", torch.cuda.get_device_name(0))

    # ---- 데이터 ----
    data = load_caches(args.ntu, args.etri)
    tr, va, ho, tr_eval, masks = build_datasets(data, cfg)
    print("\n" + "=" * 62)
    for line in summarize_split(data, masks):
        print(line)
    print("=" * 62)
    assert tr.pmean.shape[0] == cfg.n_phys, "physics 차원 불일치!"

    loaders = build_loaders(tr, va, ho, tr_eval, cfg, num_workers=args.num_workers)

    # ---- 특징 판별력 진단 (학습 전 sanity check) ----
    print("\nphysics 특징 Fall-vs-나머지 AUC (Train, 정규화 후):")
    for nm, a_all, a_ntu, a_etr in diagnose_features(data['P'], data['Y'], data['D'], masks['train']):
        print(f"  {nm:<16s} 전체 {a_all:.3f}  NTU {a_ntu:.3f}  ETRI {a_etr:.3f}")

    # ---- 모델 ----
    model = YmasNet(n_phys=cfg.n_phys, dropout=cfg.dropout).to(device)
    print(f"\ntrainable 파라미터: {count_parameters(model)/1e6:.4f}M")

    # ---- 학습 ----
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    Y_train = data['Y'][masks['train']]
    hist, best_score = train(model, loaders, cfg, device, args.out,
                             Y_train, tr.pmean, tr.pstd)

    # ---- 평가: best 체크포인트 로드 후 Val 스윕 -> th 선택 -> Holdout 1회 ----
    ck = torch.load(args.out, map_location=device, weights_only=False)
    model.load_state_dict(ck['model'])
    model.eval()
    print(f"\n로드: epoch {ck['epoch']+1} / source={ck.get('source','?')} | "
          f"val_acc {ck['val_acc']:.4f} | balR {ck.get('val_rf_bal', float('nan')):.4f}")

    _, _, _, VPROB, VYT, VDOM, _ = evaluate(model, loaders[1], device)
    rows = sweep_thresholds(VPROB, VYT, VDOM)
    print("\nVal 임계값 스윕:")
    print(f"{'th':>5} {'recall':>8} {'prec':>8} {'FA':>6}")
    for th, r, p, fa in rows:
        print(f"{th:>5.2f} {r:>8.3f} {p:>8.3f} {fa:>6d}")

    sel_th, rule, best = select_threshold(rows, precision_gate=0.95)
    print(f"\n선택된 배포 임계값: th={sel_th:.2f}  ({rule})")
    print(f"  Val -- recall {best[1]:.4f} / precision {best[2]:.4f} / 오경보 {best[3]}건")

    print(f"\nHoldout 최종 확인 (th={sel_th:.2f} 고정):")
    rep, _ = holdout_report(model, loaders[2], device, sel_th)
    o = rep['overall']
    print(f"  [전체] recall {o['recall']:.4f} | precision {o['precision']:.4f} | 오경보 {o['fa']}건")
    for name in ('NTU', 'ETRI'):
        d = rep[name]
        if d is None:
            print(f"  [{name}] 샘플 없음"); continue
        print(f"  [{name}] recall {d['recall']:.4f} | 오경보 {d['fa']}건 "
              f"(n={d['n']}, Fall {d['n_fall']}개)")

    print("\n완료. 체크포인트:", args.out)


if __name__ == '__main__':
    main()
