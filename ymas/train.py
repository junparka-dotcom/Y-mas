"""학습 루프 (v13 확정 하이퍼파라미터).

노트북 CELL 10 과 동일:
  - Focal Loss (gamma=1.5, label_smooth=0.15) + Aux Fall Head (weight=0.15)
  - Mixup (alpha=0.4)
  - EMA (decay=0.9999) 로 평가·저장
  - Cosine LR + warmup, 후반 SWA 전환
  - 도메인 균형 점수로 best 체크포인트 선택 (NTU/ETRI 동등 가중)
  - SWA 채택: BN 통계 재추정 후 Val 에서 EMA best 이길 때만
    (v13~v15 는 swa_model 을 갱신만 하고 쓰지 않아 SWA 효과가 0 이었던 버그 수정)

[!] 교훈 2: "계산했다고 반영된 게 아니다." SWA 는 반드시 평가·저장까지
    확인한다. best 체크포인트에는 model/pmean/pstd/cfg 를 함께 저장한다.
"""

import copy
import time

import numpy as np
import torch

from .config import Config
from .model import YmasNet, FocalLoss, EMA, count_parameters
from .evaluate import evaluate, domain_balanced_score, update_bn_stats


def mixup_batch(x, p, y, alpha=0.4):
    if alpha <= 0:
        return x, p, y, y, 1.0
    lam = float(np.random.beta(alpha, alpha))
    B = x.size(0)
    idx = torch.randperm(B, device=x.device)
    return lam * x + (1 - lam) * x[idx], lam * p + (1 - lam) * p[idx], y, y[idx], lam


def mixup_loss(crit, logits, ya, yb, lam):
    return lam * crit(logits, ya) + (1 - lam) * crit(logits, yb)


def set_seed(seed):
    import random as _random
    torch.manual_seed(seed)
    np.random.seed(seed)
    _random.seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def train(model, loaders, cfg: Config, device, ckpt_path, Y_train, pmean, pstd,
          verbose=True):
    """전체 학습 실행. best 체크포인트를 ckpt_path 에 저장.

    Args:
        loaders: (train_loader, val_loader, holdout_loader, train_eval_loader)
        Y_train: Train 라벨 배열 (클래스 가중치 계산용)
    Returns:
        hist dict, best_score
    """
    from torch.amp import autocast, GradScaler
    from torch.optim.swa_utils import AveragedModel, SWALR

    train_loader, val_loader, holdout_loader, train_eval_loader = loaders
    use_cuda = (device == 'cuda')

    # 클래스 불균형 보정 가중치
    cls_cnt = np.bincount(Y_train, minlength=3).astype(np.float32)
    alpha = torch.tensor(cls_cnt.sum() / (3 * np.maximum(cls_cnt, 1)),
                         dtype=torch.float32, device=device)
    alpha = alpha / alpha.mean()
    if verbose:
        print("클래스 분포:", cls_cnt.astype(int))
        print("클래스 가중치:", alpha.cpu().numpy().round(3))

    crit = FocalLoss(alpha=alpha, gamma=cfg.focal_gamma,
                     label_smoothing=cfg.label_smooth)
    crit_aux = FocalLoss(alpha=None, gamma=cfg.focal_gamma,
                         label_smoothing=cfg.label_smooth, n_cls=2)

    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr,
                            weight_decay=cfg.weight_decay)
    scaler = GradScaler(enabled=use_cuda)
    ema = EMA(model, cfg.ema_decay)

    steps_per_epoch = len(train_loader)
    total_steps = cfg.epochs * steps_per_epoch
    warmup_steps = cfg.warmup_epochs * steps_per_epoch

    def lr_lambda(step):
        if step < warmup_steps:
            return step / max(warmup_steps, 1)
        prog = (step - warmup_steps) / max(total_steps - warmup_steps, 1)
        return 0.5 * (1 + np.cos(np.pi * prog))

    sched = torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda)
    swa_model = AveragedModel(model)
    swa_start = int(cfg.epochs * cfg.swa_start_r)
    swa_sched = SWALR(opt, swa_lr=cfg.lr * 0.05)

    hist = {'train_loss': [], 'train_acc': [], 'val_acc': [], 'val_rf': [],
            'val_loss': [], 'val_rf_bal': [], 'val_acc_bal': []}
    best_score = -1.0
    t0 = time.time()

    def save_ckpt(source, v_acc, v_rf, rf_b, acc_b, epoch):
        torch.save({'model': model.state_dict(),
                    'pmean': pmean, 'pstd': pstd,
                    'cfg': cfg.to_dict(), 'epoch': epoch, 'source': source,
                    'val_acc': v_acc, 'val_rf': v_rf,
                    'val_rf_bal': rf_b, 'val_acc_bal': acc_b}, ckpt_path)

    for ep in range(cfg.epochs):
        model.train()
        tot = 0
        correct = 0
        loss_sum = 0.0
        for x, p, y, _dom in train_loader:
            x = x.to(device, non_blocking=True)
            p = p.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            xm, pm, ya, yb, lam = mixup_batch(x, p, y, cfg.mixup_alpha)
            opt.zero_grad(set_to_none=True)
            with autocast('cuda', enabled=use_cuda):
                out, aux = model(xm, pm, return_aux=True)
                loss = mixup_loss(crit, out, ya, yb, lam)
                ya2 = (ya == 2).long()
                yb2 = (yb == 2).long()
                loss += cfg.aux_weight * mixup_loss(crit_aux, aux, ya2, yb2, lam)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(opt)
            scaler.update()
            ema.update(model)
            if ep >= swa_start:
                swa_sched.step()
            else:
                sched.step()
            loss_sum += loss.item() * y.size(0)
            tot += y.size(0)
            correct += (out.argmax(1) == y).sum().item()
        if ep >= swa_start:
            swa_model.update_parameters(model)

        tr_loss = loss_sum / tot
        tr_acc = correct / tot
        orig = ema.apply(model)
        v_acc, v_rf, v_pf, VP, VY, VD, v_loss = evaluate(model, val_loader, device, criterion=crit)
        ema.restore(model, orig)

        score, rf_b, acc_b, dom_detail = domain_balanced_score(VP, VY, VD)

        hist['train_loss'].append(tr_loss)
        hist['train_acc'].append(tr_acc)
        hist['val_acc'].append(v_acc)
        hist['val_rf'].append(v_rf)
        hist['val_loss'].append(v_loss)
        hist['val_rf_bal'].append(rf_b)
        hist['val_acc_bal'].append(acc_b)

        star = ''
        if score > best_score:
            best_score = score
            star = '  *'
            orig = ema.apply(model)
            save_ckpt('EMA', v_acc, v_rf, rf_b, acc_b, ep)
            ema.restore(model, orig)

        if verbose:
            dom_s = '  '.join(f"{k} R={v[1]:.3f}" for k, v in dom_detail.items()
                              if v[1] is not None)
            print(f"[{ep+1:2d}/{cfg.epochs}] loss {tr_loss:.4f} val_loss {v_loss:.4f} | "
                  f"tr_acc {tr_acc:.4f} | val_acc {v_acc:.4f} | "
                  f"balR {rf_b:.4f} | {dom_s}{star}")

    if verbose:
        print(f"\n학습 완료 ({(time.time()-t0)/60:.1f}분)")

    # ---------------- SWA 채택 판정 ----------------
    n_avg = int(swa_model.n_averaged.item()) if hasattr(swa_model, 'n_averaged') else 0
    if n_avg == 0:
        if verbose:
            print("SWA 누적 없음 -- EMA best 유지")
    else:
        if verbose:
            print(f"\nSWA 누적 {n_avg}개 시점 평균 -- BN 재추정 후 비교")
        backup = copy.deepcopy(model.state_dict())
        model.load_state_dict(swa_model.module.state_dict())
        update_bn_stats(train_loader, model, device)
        s_acc, s_rf, s_pf, SP, SY, SD, _ = evaluate(model, val_loader, device, criterion=crit)
        swa_score, s_rf_b, s_acc_b, _ = domain_balanced_score(SP, SY, SD)
        if verbose:
            print(f"  SWA score {swa_score:.4f}  vs  EMA best {best_score:.4f}")
        if swa_score > best_score:
            if verbose:
                print("  -> SWA 채택")
            save_ckpt('SWA', s_acc, s_rf, s_rf_b, s_acc_b, cfg.epochs - 1)
            best_score = swa_score
        else:
            if verbose:
                print("  -> EMA best 유지")
            model.load_state_dict(backup)

    if verbose:
        print("\n체크포인트:", ckpt_path)
    return hist, best_score
