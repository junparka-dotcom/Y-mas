#!/usr/bin/env python3
"""오경보(false alarm) 해부 진단.

재현 베이스라인 체크포인트를 로드해, 어떤 샘플이 Fall 로 오탐되는지를
클래스 / 도메인 / 피험자 / 액션(ETRI) / P(Fall) 분포로 분해한다.

Phase 1(하드 네거티브 재가중) 전략을 데이터 근거 위에서 세우기 위한
사전 진단이다. "P39/P40 에 몰린다" 같은 문서상 표현을 넘겨짚지 않고
이번 체크포인트에서 실제 분포를 확인한다.

사용법 (Colab):
    !python scripts/diagnose_fa.py \
        --ntu "$NTU" --etri "$ETRI" --ckpt "$OUT" --th 0.75
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import torch

from ymas.config import Config, ETRI_SUBJ_OFFSET, ETRI_ACTION_NAMES
from ymas.dataset import load_caches, build_datasets, build_loaders
from ymas.model import YmasNet
from ymas.evaluate import evaluate
from ymas.skeleton import CLASS_NAMES


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ntu', required=True)
    ap.add_argument('--etri', required=True)
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--th', type=float, default=0.75)
    return ap.parse_args()


@torch.no_grad()
def probs_for_mask(model, data, mask, cfg, device, pmean, pstd):
    """주어진 마스크 샘플들의 P(Fall) 확률을 순서 유지하며 반환.

    build_loaders 는 shuffle=False 이므로 로더 순서 = 마스크 인덱스 순서.
    (Train augmentation 없이 평가하기 위해 별도 no-aug 데이터셋 구성)
    """
    from ymas.dataset import YmasDataset
    from torch.utils.data import DataLoader
    ds = YmasDataset(data['X'][mask], data['P'][mask], data['Y'][mask],
                     data['D'][mask], train=False, pmean=pmean, pstd=pstd)
    loader = DataLoader(ds, batch_size=cfg.batch_size, shuffle=False, num_workers=2)
    model.eval()
    probs = []
    for x, p, y, d in loader:
        o = model(x.to(device), p.to(device))
        probs.append(o.float().softmax(1)[:, 2].cpu().numpy())
    return np.concatenate(probs)


def dissect(tag, data, mask, pfall, th):
    """한 분할(split)의 오경보 해부 리포트 출력."""
    Y = data['Y'][mask]
    D = data['D'][mask]
    S = data['S'][mask]
    hit = pfall >= th
    fa = hit & (Y != 2)   # Fall 아닌데 Fall 로 예측
    n_nonfall = int((Y != 2).sum())
    print("\n" + "=" * 70)
    print(f"[{tag}] 오경보 해부 (th={th})")
    print("=" * 70)
    print(f"  전체 {len(Y)}개 | non-Fall {n_nonfall}개 | 오경보 {int(fa.sum())}건 "
          f"({fa.sum()/max(n_nonfall,1)*100:.1f}%)")

    # 1) 도메인 x 클래스 별 오경보
    print("\n  [도메인 x 클래스별 오경보]")
    print(f"  {'도메인':<6} {'클래스':<8} {'오경보':>6} {'non-Fall':>9} {'비율':>7}")
    for d, dn in [(0, 'NTU'), (1, 'ETRI')]:
        for c in (0, 1):   # Normal, Risk
            m = (D == d) & (Y == c)
            if m.sum() == 0:
                continue
            nfa = int(fa[m].sum())
            print(f"  {dn:<6} {CLASS_NAMES[c]:<8} {nfa:>6} {int(m.sum()):>9} "
                  f"{nfa/max(m.sum(),1)*100:>6.1f}%")

    # 2) P(Fall) 분포 (오경보 샘플)
    fa_probs = np.sort(pfall[fa])
    if len(fa_probs):
        print("\n  [오경보 P(Fall) 분포]")
        print(f"    최소 {fa_probs.min():.3f} / 중앙 {np.median(fa_probs):.3f} / "
              f"최대 {fa_probs.max():.3f}")
        print(f"    간발의 차 (th~0.80): {int(((fa_probs>=th)&(fa_probs<0.80)).sum())}건")
        print(f"    확신에 찬 오답 (>=0.90): {int((fa_probs>=0.90).sum())}건")

    # 3) 피험자별 오경보 (상위)
    print("\n  [피험자별 오경보 상위]")
    subj_fa = {}
    for s in np.unique(S):
        m = S == s
        nfa = int(fa[m].sum())
        if nfa > 0:
            dom = 'ETRI' if s >= ETRI_SUBJ_OFFSET else 'NTU'
            sid = int(s - ETRI_SUBJ_OFFSET) if s >= ETRI_SUBJ_OFFSET else int(s)
            subj_fa[(dom, sid)] = (nfa, int((m & (Y != 2)).sum()))
    for (dom, sid), (nfa, nnf) in sorted(subj_fa.items(), key=lambda kv: -kv[1][0])[:10]:
        print(f"    {dom} P{sid:<4} 오경보 {nfa:>3} / non-Fall {nnf:>4} "
              f"({nfa/max(nnf,1)*100:.1f}%)")


def analyze_train_candidates(data, mask, pfall, th):
    """Train 의 재가중 대상 분석.

    증강 없는 Train 에서 Normal/Risk 샘플이 받는 P(Fall) 분포를 본다.
    하드 네거티브 재가중이 겨냥할 '낙상 닮은 정상/위험 자세' 가 Train 에
    실제로 존재하는지, 몇 건인지 확인한다 (없으면 재가중은 헛방).
    """
    Y = data['Y'][mask]
    D = data['D'][mask]
    nonfall = Y != 2
    pf = pfall[nonfall]
    Dn = D[nonfall]
    Yn = Y[nonfall]

    print("\n" + "=" * 70)
    print("[Train] 하드 네거티브 후보 분석 (증강 없음, non-Fall 샘플의 P(Fall))")
    print("=" * 70)
    print(f"  non-Fall 총 {len(pf)}개")
    print(f"  P(Fall) 분위: p50 {np.percentile(pf,50):.3f} / "
          f"p90 {np.percentile(pf,90):.3f} / p99 {np.percentile(pf,99):.3f} / "
          f"max {pf.max():.3f}")

    print("\n  [임계 구간별 non-Fall 샘플 수 = 재가중 후보]")
    for lo, hi, name in [(th, 0.80, f'경계 [{th:.2f},0.80)'),
                         (0.80, 0.90, '위험 [0.80,0.90)'),
                         (0.90, 1.01, '확신오답 [0.90,1.0]')]:
        m = (pf >= lo) & (pf < hi)
        n_ntu = int((m & (Dn == 0)).sum())
        n_etri = int((m & (Dn == 1)).sum())
        n_risk = int((m & (Yn == 1)).sum())
        print(f"    {name:<22} 총 {int(m.sum()):>5}  "
              f"(NTU {n_ntu} / ETRI {n_etri} / 그중 Risk {n_risk})")

    hard = int((pf >= th).sum())
    print(f"\n  th={th} 이상 non-Fall (Train 에서 '오답 경향') : {hard}건 "
          f"({hard/len(pf)*100:.2f}%)")
    if hard < 20:
        print("  [!] 후보가 적음 -- 단순 재가중 효과 제한적, 증강 강화 병행 권장")
    else:
        print("  -> 재가중 대상 충분히 존재. OHEM/재가중으로 밀어낼 여지 있음")


def main():
    args = parse_args()
    cfg = Config()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print("device:", device)

    data = load_caches(args.ntu, args.etri)
    tr, va, ho, tr_eval, masks = build_datasets(data, cfg)

    model = YmasNet(n_phys=cfg.n_phys, dropout=cfg.dropout).to(device)
    ck = torch.load(args.ckpt, map_location=device, weights_only=False)
    model.load_state_dict(ck['model'])
    pmean, pstd = ck['pmean'], ck['pstd']
    print(f"체크포인트: epoch {ck['epoch']+1} / source={ck.get('source','?')}")

    for tag, mkey in [('Val', 'val'), ('Holdout', 'holdout')]:
        mask = masks[mkey]
        pfall = probs_for_mask(model, data, mask, cfg, device, pmean, pstd)
        dissect(tag, data, mask, pfall, args.th)

    # Train 재가중 후보 분석 (증강 없는 Train)
    train_mask = masks['train']
    train_pfall = probs_for_mask(model, data, train_mask, cfg, device, pmean, pstd)
    analyze_train_candidates(data, train_mask, train_pfall, args.th)

    print("\n완료.")


if __name__ == '__main__':
    main()
