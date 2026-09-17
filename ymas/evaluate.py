"""평가 · 진단 · 임계값 선택.

노트북 CELL 8(특징 판별력/도메인 시프트 진단) + CELL 11(과적합 진단 +
도메인 분리 + 임계값 스윕) 과 동일.

[!] 임계값 선택 규칙 (인수인계 교훈 1 - 가장 뼈아팠던 실수)
    반드시 precision>=0.95 게이트를 **먼저** 걸고 그 안에서 recall 최대인
    th 를 고른다. "오경보 0" 을 먼저 찾으면 label_smoothing 때문에 확률이
    0.9 근처로 몰려 th=0.80~0.95 구간에서 recall 이 절벽처럼 떨어질 때
    (0.93 -> 0.50 -> 0.01) 절벽 맨 끝이 배포 임계값으로 선택되어 버린다.
    실제로 v16 1차 학습에서 이 문제로 NTU recall 이 0.988 -> 0.06 붕괴.
"""

import numpy as np
import torch

from .skeleton import CLASS_NAMES, PHYS_NAMES


@torch.no_grad()
def evaluate(model, loader, device, criterion=None):
    """도메인(NTU/ETRI) 정보까지 반환.

    criterion 을 주면 증강 없는 loss 평균도 함께 계산 (val_loss 추적 --
    accuracy 보다 먼저 과적합 신호가 나타남).

    Returns: acc, recall_fall, precision_fall, Pa, Ya, Da, avg_loss
    """
    from torch.amp import autocast
    model.eval()
    P_all, Y_all, D_all = [], [], []
    loss_sum = 0.0
    n = 0
    use_cuda = (str(device) == 'cuda' or (hasattr(device, 'type') and device.type == 'cuda'))
    for x, p, y, dom in loader:
        x_d, p_d, y_d = x.to(device), p.to(device), y.to(device)
        with autocast('cuda', enabled=use_cuda):
            o = model(x_d, p_d)
            if criterion is not None:
                loss_sum += criterion(o, y_d).item() * y.size(0)
                n += y.size(0)
        P_all.append(o.float().softmax(1).cpu())
        Y_all.append(y)
        D_all.append(dom)
    Pa = torch.cat(P_all).numpy()
    Ya = torch.cat(Y_all).numpy()
    Da = torch.cat(D_all).numpy()
    pred = Pa.argmax(1)
    acc = (pred == Ya).mean()
    rf = ((pred == 2) & (Ya == 2)).sum() / max((Ya == 2).sum(), 1)
    pf = ((pred == 2) & (Ya == 2)).sum() / max((pred == 2).sum(), 1)
    avg_loss = loss_sum / n if criterion is not None else None
    return acc, rf, pf, Pa, Ya, Da, avg_loss


def domain_balanced_score(Pa, Ya, Da, w_recall=0.6):
    """NTU 와 ETRI 를 동등 가중해 하나의 점수로 합친다.

    단순 전체 평균을 쓰면 샘플이 많은 도메인이 점수를 지배해서
    '한쪽 도메인만 잘하는 모델' 이 best 로 뽑힐 수 있다.

    Returns: (score, recall_balanced, acc_balanced, detail dict)
    """
    accs, rfs, detail = [], [], {}
    for d in (0, 1):
        m = Da == d
        if m.sum() == 0:
            continue
        pred = Pa[m].argmax(1)
        y = Ya[m]
        a = (pred == y).mean()
        accs.append(a)
        n_fall = (y == 2).sum()
        r = ((pred == 2) & (y == 2)).sum() / n_fall if n_fall > 0 else None
        if r is not None:
            rfs.append(r)
        detail['NTU' if d == 0 else 'ETRI'] = (a, r, int(m.sum()))
    acc_b = float(np.mean(accs)) if accs else 0.0
    rf_b = float(np.mean(rfs)) if rfs else 0.0
    return w_recall * rf_b + (1 - w_recall) * acc_b, rf_b, acc_b, detail


@torch.no_grad()
def update_bn_stats(loader, m, device):
    """SWA 가중치는 여러 시점의 평균이라 BatchNorm running 통계가 실제
    가중치와 맞지 않는다. 한 epoch 분 데이터를 흘려 재추정한다.
    (torch 기본 update_bn 은 (x,y) 로더만 받아서 직접 구현.)
    """
    bns = [mod for mod in m.modules()
           if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm)]
    if not bns:
        return
    momenta = {}
    for bn in bns:
        bn.reset_running_stats()
        momenta[bn] = bn.momentum
        bn.momentum = None
    was_training = m.training
    m.train()
    for x, p, _y, _d in loader:
        m(x.to(device), p.to(device))
    for bn in bns:
        bn.momentum = momenta[bn]
    m.train(was_training)


def rank_auc(scores, is_pos):
    """순위 기반 AUC. 동점은 평균 순위로 처리."""
    scores = np.asarray(scores, dtype=np.float64)
    is_pos = np.asarray(is_pos, dtype=bool)
    n_pos = int(is_pos.sum())
    n_neg = int((~is_pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float('nan')
    order = np.argsort(scores, kind='mergesort')
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    s_sorted = scores[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return (ranks[is_pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def sweep_thresholds(VPROB, VYT, VDOM, ths=None):
    """Val 확률로 임계값 스윕. rows = [(th, recall_all, precision_all, fa_all), ...]."""
    if ths is None:
        ths = np.round(np.arange(0.30, 0.96, 0.05), 2)
    rows = []
    for th in ths:
        hit = VPROB[:, 2] >= th
        r_all = (hit & (VYT == 2)).sum() / max((VYT == 2).sum(), 1)
        p_all = (hit & (VYT == 2)).sum() / max(hit.sum(), 1)
        fa_all = int((hit & (VYT != 2)).sum())
        rows.append((float(th), float(r_all), float(p_all), fa_all))
    return rows


def select_threshold(sweep_rows, precision_gate=0.95):
    """배포 임계값 선택 (교훈 1: precision 게이트 우선).

    1) precision>=gate 중 recall 최대
    2) 없으면 오경보 0 중 recall 최대
    3) 그래도 없으면 0.6*recall + 0.4*precision 최대

    Returns: (selected_th, rule_str, best_row)
    """
    high_p = [r for r in sweep_rows if r[2] >= precision_gate]
    zero_fa = [r for r in sweep_rows if r[3] == 0]
    if high_p:
        best = max(high_p, key=lambda r: r[1])
        rule = f"precision>={precision_gate} 중 recall 최대"
    elif zero_fa:
        best = max(zero_fa, key=lambda r: r[1])
        rule = f"precision>={precision_gate} 만족 th 없음 -> 오경보 0 중 recall 최대"
    else:
        best = max(sweep_rows, key=lambda r: 0.6 * r[1] + 0.4 * r[2])
        rule = "0.6*recall+0.4*precision 최대"
    return float(best[0]), rule, best


def holdout_report(model, holdout_loader, device, th):
    """선택된 th 하나로 Holdout 최종 확인 (스윕 없음, 단 1회).

    Returns dict: overall + per-domain recall/precision/fa.
    """
    acc, rf, pf, PROB, YT, DOM, _ = evaluate(model, holdout_loader, device)
    hit = PROB[:, 2] >= th
    out = {}
    out['overall'] = dict(
        recall=float((hit & (YT == 2)).sum() / max((YT == 2).sum(), 1)),
        precision=float((hit & (YT == 2)).sum() / max(hit.sum(), 1)),
        fa=int((hit & (YT != 2)).sum()),
    )
    for dcode, name in [(0, 'NTU'), (1, 'ETRI')]:
        m = DOM == dcode
        if m.sum() == 0:
            out[name] = None
            continue
        out[name] = dict(
            recall=float((hit[m] & (YT[m] == 2)).sum() / max((YT[m] == 2).sum(), 1)),
            fa=int((hit[m] & (YT[m] != 2)).sum()),
            n=int(m.sum()),
            n_fall=int((YT[m] == 2).sum()),
        )
    return out, (PROB, YT, DOM)


def diagnose_features(P, Y, D, train_mask):
    """physics 특징별 Fall-vs-나머지 AUC (정규화 후 값, Train 구간)."""
    Ptr, Ytr, Dtr = P[train_mask], Y[train_mask], D[train_mask]
    rows = []
    for k, nm in enumerate(PHYS_NAMES):
        a_all = rank_auc(Ptr[:, k], Ytr == 2)
        a_ntu = rank_auc(Ptr[Dtr == 0][:, k], Ytr[Dtr == 0] == 2)
        a_etr = rank_auc(Ptr[Dtr == 1][:, k], Ytr[Dtr == 1] == 2)
        rows.append((nm, a_all, a_ntu, a_etr))
    return rows
