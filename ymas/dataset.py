"""캐시 로드 + NTU/ETRI 병합 + 피험자 분할 + Dataset/DataLoader.

노트북 CELL 5/6(캐시 로드 부분) + CELL 7 과 동일.

캐시 포맷 (v15):
  ntu_ymas_v15_T64.npz  : X[N,T,25,3], P[N,10], Y[N], S[N]
  etri_ymas_v15_T64.npz : X[N,T,25,3], P[N,10], Y[N], S[N], A[N]

도메인 라벨 D: 0=NTU, 1=ETRI. (향후 실데이터는 2 로 확장 예정.)
pmean/pstd 는 반드시 Train 분할에서만 계산해 Val/Holdout 에 전파한다
(누수 방지). 추론 시에는 체크포인트에 저장된 pmean/pstd 를 그대로 쓴다.
"""

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

from .config import (
    Config,
    TRAIN_SUBJECTS, VAL_SUBJECTS, HOLDOUT_SUBJECTS,
    ETRI_TRAIN_SUBJECTS, ETRI_VAL_SUBJECTS, ETRI_HOLDOUT_SUBJECTS,
    ETRI_SUBJ_OFFSET,
)
from .preprocess import to_streams, temporal_crop
from .skeleton import CLASS_NAMES


def load_caches(ntu_path, etri_path):
    """NTU + ETRI 캐시를 로드해 병합한 배열을 반환.

    Returns dict with X, P, Y, S, D  (D: 0=NTU, 1=ETRI).
    """
    dn = np.load(ntu_path, allow_pickle=True)
    de = np.load(etri_path, allow_pickle=True)
    X = np.concatenate([dn['X'], de['X']], axis=0)
    P = np.concatenate([dn['P'], de['P']], axis=0)
    Y = np.concatenate([dn['Y'], de['Y']], axis=0)
    S = np.concatenate([dn['S'], de['S']], axis=0)
    D = np.concatenate([
        np.zeros(len(dn['Y']), dtype=np.int64),   # 0 = NTU
        np.ones(len(de['Y']), dtype=np.int64),    # 1 = ETRI
    ])
    return dict(X=X, P=P, Y=Y, S=S, D=D)


def make_masks(S):
    """피험자 ID 배열 -> train/val/holdout boolean 마스크."""
    train_all = TRAIN_SUBJECTS | {s + ETRI_SUBJ_OFFSET for s in ETRI_TRAIN_SUBJECTS}
    val_all = VAL_SUBJECTS | {s + ETRI_SUBJ_OFFSET for s in ETRI_VAL_SUBJECTS}
    holdout_all = HOLDOUT_SUBJECTS | {s + ETRI_SUBJ_OFFSET for s in ETRI_HOLDOUT_SUBJECTS}
    return (
        np.isin(S, list(train_all)),
        np.isin(S, list(val_all)),
        np.isin(S, list(holdout_all)),
    )


class YmasDataset(Dataset):
    """스켈레톤 3-스트림 + physics 특징 데이터셋.

    train=True 일 때만 augmentation 적용:
      - Y축 랜덤 회전 (+-0.26 rad)
      - 가우시안 노이즈 (std 0.01)
      - 프레임 드롭 (30% 확률로 10% 프레임을 직전 프레임으로 치환)
      - 스케일 지터 (0.9~1.1)
      - 시간축 크롭 (50% 확률)
    """

    def __init__(self, X, P, Y, D, train, pmean=None, pstd=None):
        self.X = X
        self.P = P
        self.Y = Y
        self.D = D
        self.train = train
        self.pmean = P.mean(0) if pmean is None else pmean
        self.pstd = P.std(0) + 1e-6 if pstd is None else pstd
        self.T = X.shape[1]

    def __len__(self):
        return len(self.Y)

    def __getitem__(self, i):
        x = self.X[i].copy()
        if self.train:
            th = np.random.uniform(-0.26, 0.26)
            c, s = np.cos(th), np.sin(th)
            R = np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]], dtype=np.float32)
            x = x @ R.T
            x += np.random.normal(0, 0.01, x.shape).astype(np.float32)
            if np.random.rand() < 0.3:
                mask = np.random.rand(len(x)) < 0.1
                for t in np.where(mask)[0]:
                    if t > 0:
                        x[t] = x[t - 1]
            x *= np.random.uniform(0.9, 1.1)
            if np.random.rand() < 0.5:
                x = temporal_crop(x, self.T)
        p = (self.P[i] - self.pmean) / self.pstd
        return (torch.from_numpy(to_streams(x)).float(),
                torch.from_numpy(p).float(),
                int(self.Y[i]), int(self.D[i]))


def seed_worker(worker_id, base_seed):
    """DataLoader worker 별 시드 고정 (augmentation 재현성)."""
    import random as _random
    ws = base_seed + worker_id
    np.random.seed(ws)
    _random.seed(ws)


def build_datasets(data, cfg: Config):
    """병합 데이터 + config -> (tr, va, ho, tr_eval) 데이터셋.

    pmean/pstd 는 Train 에서만 계산해 나머지에 전파한다.
    """
    X, P, Y, D, S = data['X'], data['P'], data['Y'], data['D'], data['S']
    train_mask, val_mask, holdout_mask = make_masks(S)

    tr = YmasDataset(X[train_mask], P[train_mask], Y[train_mask], D[train_mask],
                     train=True)
    va = YmasDataset(X[val_mask], P[val_mask], Y[val_mask], D[val_mask],
                     train=False, pmean=tr.pmean, pstd=tr.pstd)
    ho = YmasDataset(X[holdout_mask], P[holdout_mask], Y[holdout_mask], D[holdout_mask],
                     train=False, pmean=tr.pmean, pstd=tr.pstd)
    # 증강 없는 Train 평가용 (암기/과적합 진단 전용)
    tr_eval = YmasDataset(X[train_mask], P[train_mask], Y[train_mask], D[train_mask],
                          train=False, pmean=tr.pmean, pstd=tr.pstd)
    masks = dict(train=train_mask, val=val_mask, holdout=holdout_mask)
    return tr, va, ho, tr_eval, masks


def build_loaders(tr, va, ho, tr_eval, cfg: Config, num_workers=2):
    """데이터셋 -> DataLoader 4종. 재현성을 위해 시드/제너레이터 고정."""
    import functools
    g = torch.Generator()
    g.manual_seed(cfg.seed)
    worker_fn = functools.partial(seed_worker, base_seed=cfg.seed)

    train_loader = DataLoader(
        tr, batch_size=cfg.batch_size, shuffle=True, num_workers=num_workers,
        drop_last=True, pin_memory=True, worker_init_fn=worker_fn, generator=g)
    val_loader = DataLoader(
        va, batch_size=cfg.batch_size, shuffle=False, num_workers=num_workers,
        pin_memory=True)
    holdout_loader = DataLoader(
        ho, batch_size=cfg.batch_size, shuffle=False, num_workers=num_workers,
        pin_memory=True)
    train_eval_loader = DataLoader(
        tr_eval, batch_size=cfg.batch_size, shuffle=False, num_workers=num_workers,
        pin_memory=True)
    return train_loader, val_loader, holdout_loader, train_eval_loader


def summarize_split(data, masks):
    """분할별 샘플 수 / 도메인 / 클래스 분포 요약 문자열 리스트 반환."""
    Y, D = data['Y'], data['D']
    lines = []
    total = sum(m.sum() for m in masks.values())
    for label, m in [('Train', masks['train']), ('Val', masks['val']),
                     ('Holdout', masks['holdout'])]:
        n = int(m.sum())
        n_ntu = int((D[m] == 0).sum())
        n_etri = int((D[m] == 1).sum())
        lines.append(f"{label:>8}  {n:>7}개 ({n/total*100:4.1f}%)  "
                     f"NTU {n_ntu:>6} / ETRI {n_etri:>6}")
    return lines
