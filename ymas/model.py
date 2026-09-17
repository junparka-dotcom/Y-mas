"""Y-mas Tier 2 모델 정의 (v17).

구조 (검증된 v13~v17 유지, 파라미터 약 1.50M):
  data_bn -> STGCNBlock x7 -> TemporalAttentionPool(256)
           + physics MLP(n_phys -> 64)
           -> head(3) + aux_head(2, Fall-vs-나머지)

각 STGCNBlock:
  Adaptive Graph Adjacency (A + 학습가능 PA)
  + MultiScaleTCN (커널 3/5/9 병렬)

경량성이 아키텍처 선택의 핵심 조건이었다 (엣지 실시간성).
더 무거운 스켈레톤 모델(2s-AGCN, MS-G3D, CTR-GCN, ST-TR)은
과적합·엣지 실시간성 위반으로 폐기되었다 -- 이 구조를 임의로
키우지 말 것.
"""

import copy

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as Fn

from .skeleton import NTU_BONES, J_SPINE_SHOULDER


def build_adjacency(V=25, bones=NTU_BONES, center=J_SPINE_SHOULDER):
    """ST-GCN 공간 분할 인접행렬 3개 (self / inward / outward) 생성.

    hop 거리를 이용해 각 이웃을 '중심에 가까워지는 방향(inward)' 과
    '멀어지는 방향(outward)' 으로 나눈다 (Yan et al. ST-GCN 공간 구성).
    """
    A = np.zeros((V, V), dtype=np.float32)
    for a, b in bones:
        A[a - 1, b - 1] = 1
        A[b - 1, a - 1] = 1
    INF = 10 ** 6
    hop = np.full((V, V), INF)
    np.fill_diagonal(hop, 0)
    reach = np.eye(V)
    for d in range(1, V):
        reach = ((reach @ (A + np.eye(V))) > 0).astype(np.float32)
        hop[(reach > 0) & (hop == INF)] = d
    dc = hop[center]
    Ss = np.eye(V, dtype=np.float32)
    Si = np.zeros((V, V), dtype=np.float32)
    So = np.zeros((V, V), dtype=np.float32)
    for ii in range(V):
        for jj in range(V):
            if A[ii, jj] == 0:
                continue
            if dc[jj] < dc[ii]:
                Si[ii, jj] = 1
            else:
                So[ii, jj] = 1

    def norm(M):
        D = M.sum(0)
        D[D == 0] = 1
        return M / D

    return np.stack([norm(Ss), norm(Si), norm(So)])


class MultiScaleTCN(nn.Module):
    """커널 3/5/9 병렬 -> 빠른낙상/느린낙상 동시 포착."""

    def __init__(self, cin, cout, stride=1):
        super().__init__()
        branch_c = cout // 3
        rest_c = cout - branch_c * 2
        self.b1 = nn.Sequential(
            nn.Conv2d(cin, branch_c, (3, 1), (stride, 1), (1, 0)),
            nn.BatchNorm2d(branch_c))
        self.b2 = nn.Sequential(
            nn.Conv2d(cin, branch_c, (5, 1), (stride, 1), (2, 0)),
            nn.BatchNorm2d(branch_c))
        self.b3 = nn.Sequential(
            nn.Conv2d(cin, rest_c, (9, 1), (stride, 1), (4, 0)),
            nn.BatchNorm2d(rest_c))

    def forward(self, x):
        return torch.cat([self.b1(x), self.b2(x), self.b3(x)], dim=1)


class STGCNBlock(nn.Module):
    """Adaptive Graph GCN + MultiScaleTCN + residual."""

    def __init__(self, cin, cout, A, stride=1, residual=True):
        super().__init__()
        K = A.shape[0]
        self.register_buffer('A', torch.from_numpy(A))
        self.PA = nn.Parameter(torch.zeros_like(torch.from_numpy(A)))
        self.gcn = nn.Conv2d(cin, cout * K, 1)
        self.K = K
        self.cout = cout
        self.bn_g = nn.BatchNorm2d(cout)
        self.tcn = MultiScaleTCN(cout, cout, stride)
        if not residual:
            self.res = lambda x: 0
        elif cin == cout and stride == 1:
            self.res = nn.Identity()
        else:
            self.res = nn.Sequential(
                nn.Conv2d(cin, cout, 1, (stride, 1)),
                nn.BatchNorm2d(cout))
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        r = self.res(x)
        N, _, T, V = x.shape
        y = self.gcn(x).view(N, self.K, self.cout, T, V)
        y = torch.einsum('nkctv,kvw->nctw', y, self.A + self.PA)
        y = self.relu(self.bn_g(y))
        return self.relu(self.tcn(y) + r)


class TemporalAttentionPool(nn.Module):
    """프레임별 중요도 학습 가중평균 -> 낙상 순간 집중."""

    def __init__(self, channels):
        super().__init__()
        self.att = nn.Sequential(
            nn.Conv2d(channels, channels // 4, 1),
            nn.BatchNorm2d(channels // 4), nn.ReLU(inplace=True),
            nn.Conv2d(channels // 4, 1, 1))

    def forward(self, x):
        v_pool = x.mean(dim=3, keepdim=True)
        score = self.att(v_pool)
        w = torch.softmax(score, dim=2)
        return (v_pool * w).sum(dim=2).flatten(1)


class YmasNet(nn.Module):
    """v15+: n_phys=10 (spine_span, shoulder_asym 포함)."""

    def __init__(self, in_ch=9, n_cls=3, n_phys=10, V=25, dropout=0.45):
        super().__init__()
        A = build_adjacency(V)
        self.data_bn = nn.BatchNorm1d(in_ch * V)
        self.layers = nn.ModuleList([
            STGCNBlock(in_ch, 64, A, residual=False), STGCNBlock(64, 64, A),
            STGCNBlock(64, 64, A), STGCNBlock(64, 128, A, stride=2),
            STGCNBlock(128, 128, A), STGCNBlock(128, 256, A, stride=2),
            STGCNBlock(256, 256, A)])
        self.att_pool = TemporalAttentionPool(256)
        self.phys = nn.Sequential(
            nn.Linear(n_phys, 64), nn.BatchNorm1d(64), nn.ReLU(),
            nn.Linear(64, 64), nn.ReLU())
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(256 + 64, n_cls))
        self.aux_head = nn.Sequential(nn.Dropout(dropout), nn.Linear(256 + 64, 2))

    def forward(self, x, p, return_aux=False):
        N, C, T, V = x.shape
        x = (self.data_bn(x.permute(0, 1, 3, 2).reshape(N, C * V, T))
                 .reshape(N, C, V, T).permute(0, 1, 3, 2))
        for l in self.layers:
            x = l(x)
        g = self.att_pool(x)
        feat = torch.cat([g, self.phys(p)], dim=1)
        out = self.head(feat)
        if return_aux:
            return out, self.aux_head(feat)
        return out


class FocalLoss(nn.Module):
    """label smoothing 을 반영한 focal loss.

    v13 그리드서치 확정값: gamma=1.5, label_smoothing=0.15.
    """

    def __init__(self, alpha=None, gamma=2.0, label_smoothing=0.0, n_cls=3):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.ls = label_smoothing
        self.n = n_cls

    def forward(self, logits, targets):
        smooth = self.ls / self.n
        oh = torch.zeros_like(logits).scatter_(1, targets.unsqueeze(1), 1.0)
        oh = oh * (1 - self.ls) + smooth
        lp = Fn.log_softmax(logits, dim=1)
        pt = (lp.exp() * oh).sum(1)
        fw = (1 - pt) ** self.gamma
        if self.alpha is not None:
            fw = self.alpha[targets] * fw
        return -(fw * (lp * oh).sum(1)).mean()


class EMA:
    """Exponential Moving Average of model weights (decay=0.9999)."""

    def __init__(self, model, decay=0.9999):
        self.decay = decay
        self.step = 0
        self.shadow = copy.deepcopy(model.state_dict())

    def _d(self):
        return min(self.decay, (1 + self.step) / (10 + self.step))

    @torch.no_grad()
    def update(self, model):
        self.step += 1
        d = self._d()
        for k, v in model.state_dict().items():
            self.shadow[k] = d * self.shadow[k] + (1 - d) * v.float()

    def apply(self, model):
        orig = copy.deepcopy(model.state_dict())
        model.load_state_dict(self.shadow)
        return orig

    def restore(self, model, orig):
        model.load_state_dict(orig)


def count_parameters(model):
    return sum(p.numel() for p in model.parameters())
