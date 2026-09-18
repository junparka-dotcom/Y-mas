#!/usr/bin/env python3
"""임계값(th) 재분석 — 학습 없이 저장된 체크포인트로 트레이드오프 분석.

목적: augmentation 강화(v18) 후 오경보가 경계 구간(0.75~0.80)으로 밀려났다.
      th 를 올리면 오경보를 더 줄일 수 있는지, 그 대가로 도메인별 recall
      (특히 NTU 동적 낙상)이 얼마나 희생되는지를 정밀하게 본다.

방법론 (인수인계 교훈 1 준수):
  - 임계값 스윕/선택은 **Val 로만** 한다 (0.01 간격 촘촘히, 도메인별 표시).
  - precision>=gate 게이트를 먼저 걸고 그 안에서 recall 최대 th 선택.
  - Holdout 은 선택된 후보 th 각각에 대해 '참고용'으로 보여주되, 최종
    배포 th 는 Val 기준으로 정한다 (Holdout 을 보고 고르면 Holdout 의
    '미답 데이터' 의미가 옅어진다).

사용법 (Colab):
    !python scripts/analyze_threshold.py \
        --ntu "$NTU" --etri "$ETRI" --ckpt "$OUT_V18"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import torch

from ymas.config import Config, load_config
from ymas.dataset import load_caches, build_datasets, build_loaders
from ymas.model import YmasNet
from ymas.evaluate import evaluate


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ntu', required=True)
    ap.add_argument('--etri', required=True)
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--config', default=None, help='체크포인트 학습에 쓴 config (aug 무관, 구조만 필요)')
    ap.add_argument('--precision-gate', type=float, default=0.95)
    ap.add_argument('--step', type=float, default=0.01)
    return ap.parse_args()


def sweep_per_domain(PROB, YT, DOM, ths):
    """th 별 전체 + 도메인별 (recall, precision, FA) 반환."""
    rows = []
    for th in ths:
        hit = PROB[:, 2] >= th
        rec = {}
        # 전체
        r = (hit & (YT == 2)).sum() / max((YT == 2).sum(), 1)
        p = (hit & (YT == 2)).sum() / max(hit.sum(), 1)
        fa = int((hit & (YT != 2)).sum())
        rec['all'] = (float(r), float(p), fa)
        # 도메인별
        for d, name in [(0, 'NTU'), (1, 'ETRI')]:
            m = DOM == d
            rr = (hit[m] & (YT[m] == 2)).sum() / max((YT[m] == 2).sum(), 1)
            ff = int((hit[m] & (YT[m] != 2)).sum())
            rec[name] = (float(rr), ff)
        rows.append((float(th), rec))
    return rows


def main():
    args = parse_args()
    cfg = load_config(args.config)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print("device:", device, "| ckpt:", os.path.basename(args.ckpt))

    data = load_caches(args.ntu, args.etri)
    tr, va, ho, tr_eval, masks = build_datasets(data, cfg)
    loaders = build_loaders(tr, va, ho, tr_eval, cfg, num_workers=2)

    model = YmasNet(n_phys=cfg.n_phys, dropout=cfg.dropout).to(device)
    ck = torch.load(args.ckpt, map_location=device, weights_only=False)
    model.load_state_dict(ck['model'])
    model.eval()
    print(f"로드: epoch {ck['epoch']+1} / source={ck.get('source','?')} | "
          f"val_acc {ck['val_acc']:.4f}")

    _, _, _, VPROB, VYT, VDOM, _ = evaluate(model, loaders[1], device)
    _, _, _, HPROB, HYT, HDOM, _ = evaluate(model, loaders[2], device)

    ths = np.round(np.arange(0.70, 0.901, args.step), 2)
    vrows = sweep_per_domain(VPROB, VYT, VDOM, ths)
    hrows = sweep_per_domain(HPROB, HYT, HDOM, ths)
    hmap = {r[0]: r[1] for r in hrows}

    print("\n" + "=" * 100)
    print("Val 촘촘 스윕 (선택 기준) + Holdout 참고값")
    print("=" * 100)
    print(f"{'th':>5} | {'V_rec':>6} {'V_prec':>7} {'V_FA':>5} "
          f"{'V_NTUr':>7} {'V_ETRr':>7} || "
          f"{'H_rec':>6} {'H_FA':>5} {'H_NTUr':>7} {'H_NTUfa':>8} {'H_ETRr':>7}")
    print("-" * 100)
    for th, vr in vrows:
        hr = hmap[th]
        va_all = vr['all']; vn = vr['NTU']; ve = vr['ETRI']
        ha_all = hr['all']; hn = hr['NTU']; he = hr['ETRI']
        gate = '*' if va_all[1] >= args.precision_gate else ' '
        print(f"{th:>5.2f}{gate}| {va_all[0]:>6.3f} {va_all[1]:>7.3f} {va_all[2]:>5d} "
              f"{vn[0]:>7.3f} {ve[0]:>7.3f} || "
              f"{ha_all[0]:>6.3f} {ha_all[2]:>5d} {hn[0]:>7.3f} {hn[1]:>8d} {he[0]:>7.3f}")
    print("-" * 100)
    print("* = Val precision >= gate. (V_=Val, H_=Holdout, NTUr=NTU recall, NTUfa=NTU 오경보)")

    # precision 게이트 만족하는 Val th 중 recall 최대 선택 (교훈 1)
    cand = [(th, vr) for th, vr in vrows if vr['all'][1] >= args.precision_gate]
    if cand:
        best_th, best_vr = max(cand, key=lambda x: x[1]['all'][0])
        hr = hmap[best_th]
        print(f"\n[선택] Val precision>={args.precision_gate} 중 recall 최대 -> th={best_th:.2f}")
        print(f"  Val     : recall {best_vr['all'][0]:.4f} / precision {best_vr['all'][1]:.4f} / FA {best_vr['all'][2]}")
        print(f"  Holdout : recall {hr['all'][0]:.4f} / FA {hr['all'][2]} "
              f"(NTU r {hr['NTU'][0]:.4f} fa {hr['NTU'][1]} / ETRI r {hr['ETRI'][0]:.4f} fa {hr['ETRI'][1]})")
    else:
        print(f"\n[!] Val precision>={args.precision_gate} 를 만족하는 th 가 이 범위에 없음")

    print("\n완료.")


if __name__ == '__main__':
    main()
