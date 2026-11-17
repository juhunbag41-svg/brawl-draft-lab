#!/usr/bin/env python3
"""수집한 게임을 맵별 통계로 집계해 웹사이트용 stats.json 을 만든다.

모델 (맵마다 따로 적합)
  logit P(b팀 승리) = Σ_{i∈b} β_i − Σ_{j∈r} β_j            ... (1) 브롤러 강도
                    + Σ_{b팀 쌍} γ_ab − Σ_{r팀 쌍} γ_ab     ... (2) 같은 팀 시너지
                    + Σ_{a∈b, c∈r} δ_ac                    ... (3) 상대 카운터 (δ_ca = −δ_ac)

추정 방법
  (1) β: L2 정규화(릿지) 로지스틱 회귀를 뉴턴 방법으로 적합한다.
      정규화는 β ~ N(0, 1/λ) 사전분포와 같아서, 판수가 적은 브롤러의 강도를 0(평균) 쪽으로 줄인다.
  (2)(3) γ, δ: β 모델을 고정한 상태에서 해당 쌍 변수 하나를 추가했을 때의
      정규화 뉴턴 1단계 추정값  Σ z(y−p) / (Σ z²p(1−p) + λ_pair).
      쌍마다 독립적으로 추정하므로 쌍끼리의 상관은 무시한다(한계로 명시).
      우연한 잡음을 막기 위해 점수검정 |z| ≥ PAIR_Z 인 쌍만 남긴다.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

STORE = Path(os.environ.get("STORE_DIR", "store"))
OUT = Path(os.environ.get("OUT_FILE", "site/data/stats.json"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
LAMBDA_BETA = float(os.environ.get("LAMBDA_BETA", "10"))    # 사전 표준편차 1/√10 ≈ 0.32 (logit)
LAMBDA_PAIR = float(os.environ.get("LAMBDA_PAIR", "50"))
# 쌍 효과 유의성 기준: 점수검정 통계량 |Σz(y−p)| / √(Σz²p(1−p)).
# 쌍을 수천 개 검사하므로(다중 비교) 보통의 1.96 대신 3.0(양측 p≈0.0027)을 쓴다.
PAIR_Z = float(os.environ.get("PAIR_Z", "3.0"))
MIN_MAP_GAMES = int(os.environ.get("MIN_MAP_GAMES", "200"))
MIN_PAIR_GAMES = int(os.environ.get("MIN_PAIR_GAMES", "30"))
MIN_PAIR_EFFECT = float(os.environ.get("MIN_PAIR_EFFECT", "0.01"))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def load_games():
    today = dt.datetime.now(dt.timezone.utc).date()
    cutoff = today - dt.timedelta(days=KEEP_DAYS)
    by_map = defaultdict(list)
    modes = {}
    for f in sorted((STORE / "games").glob("*.jsonl.gz")):
        try:
            if dt.date.fromisoformat(f.name[:10]) < cutoff:
                continue
        except ValueError:
            continue
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    g = json.loads(line)
                except ValueError:
                    continue
                if len(g.get("b", [])) != 3 or len(g.get("r", [])) != 3:
                    continue
                by_map[g["m"]].append(g)
                if g.get("md"):
                    modes[g["m"]] = g["md"]
    return by_map, modes


def fit_beta(B, R, y, n_brawlers, lam):
    """릿지 로지스틱 회귀. B, R: (n,3) 정수 id 배열, y: (n,) 0/1."""
    N = n_brawlers
    beta = np.zeros(N)
    cols = [(B[:, i], 1.0) for i in range(3)] + [(R[:, i], -1.0) for i in range(3)]
    for _ in range(50):
        z = beta[B].sum(1) - beta[R].sum(1)
        p = sigmoid(z)
        r = y - p
        w = p * (1 - p)
        grad = -lam * beta
        for ids, s in cols:
            grad += np.bincount(ids, weights=s * r, minlength=N)
        H = lam * np.eye(N)
        for ids_j, s_j in cols:
            for ids_k, s_k in cols:
                H += np.bincount(ids_j * N + ids_k, weights=s_j * s_k * w,
                                 minlength=N * N).reshape(N, N)
        step = np.linalg.solve(H, grad)
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break
    p = sigmoid(beta[B].sum(1) - beta[R].sum(1))
    return beta, p


def pair_effects(B, R, y, p, N, lam):
    r = y - p
    w = p * (1 - p)
    combos = [(0, 1), (0, 2), (1, 2)]

    # 시너지: z = +1 (둘 다 b팀), −1 (둘 다 r팀)
    s_num = np.zeros(N * N); s_den = np.zeros(N * N); s_cnt = np.zeros(N * N)
    for T, sign in ((B, 1.0), (R, -1.0)):
        for i, j in combos:
            lo = np.minimum(T[:, i], T[:, j]); hi = np.maximum(T[:, i], T[:, j])
            key = lo * N + hi
            s_num += np.bincount(key, weights=sign * r, minlength=N * N)
            s_den += np.bincount(key, weights=w, minlength=N * N)
            s_cnt += np.bincount(key, minlength=N * N)
    syn = s_num / (s_den + lam)
    syn_z = s_num / np.sqrt(s_den + 1e-12)

    # 카운터: 순서쌍 (lo, hi) 에 대해 z = +1 (lo가 b팀, hi가 r팀), −1 (반대)
    c_num = np.zeros(N * N); c_den = np.zeros(N * N); c_cnt = np.zeros(N * N)
    for i in range(3):
        for j in range(3):
            a = B[:, i]; c = R[:, j]
            mask = a != c
            a, c, rr, ww = a[mask], c[mask], r[mask], w[mask]
            lo = np.minimum(a, c); hi = np.maximum(a, c)
            sign = np.where(a == lo, 1.0, -1.0)
            key = lo * N + hi
            c_num += np.bincount(key, weights=sign * rr, minlength=N * N)
            c_den += np.bincount(key, weights=ww, minlength=N * N)
            c_cnt += np.bincount(key, minlength=N * N)
    ctr = c_num / (c_den + lam)
    ctr_z = c_num / np.sqrt(c_den + 1e-12)
    return syn, syn_z, s_cnt, ctr, ctr_z, c_cnt


def analyze_map(games, names, index):
    N = len(names)
    B = np.array([[index[x] for x in g["b"]] for g in games], dtype=np.int64)
    R = np.array([[index[x] for x in g["r"]] for g in games], dtype=np.int64)
    y = np.array([g["w"] for g in games], dtype=np.float64)

    beta, p = fit_beta(B, R, y, N, LAMBDA_BETA)
    syn, syn_z, s_cnt, ctr, ctr_z, c_cnt = pair_effects(B, R, y, p, N, LAMBDA_PAIR)

    appear = np.bincount(B.ravel(), minlength=N) + np.bincount(R.ravel(), minlength=N)
    wins = (np.bincount(B.ravel(), weights=np.repeat(y, 3), minlength=N)
            + np.bincount(R.ravel(), weights=np.repeat(1 - y, 3), minlength=N))

    brawlers = {}
    for i, name in enumerate(names):
        if appear[i] > 0:
            brawlers[name] = [int(appear[i]), int(wins[i]), round(float(beta[i]), 4)]

    def pairs(values, zstat, counts):
        out = {}
        keep = (counts >= MIN_PAIR_GAMES) & (np.abs(zstat) >= PAIR_Z)
        for key in np.nonzero(keep)[0]:
            v = float(values[key])
            if abs(v) >= MIN_PAIR_EFFECT:
                lo, hi = divmod(int(key), N)
                out[f"{names[lo]}|{names[hi]}"] = [round(v, 4), int(counts[key])]
        return out

    # 예측력 점검: 같은 데이터에서의 로그 손실(동전 던지기 = 0.6931)
    eps = 1e-12
    logloss = float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))
    return {
        "games": len(games),
        "brawlers": brawlers,
        "syn": pairs(syn, syn_z, s_cnt),
        "ctr": pairs(ctr, ctr_z, c_cnt),
        "fit": {"logloss": round(logloss, 4), "baseline": 0.6931},
    }


def main() -> int:
    by_map, modes = load_games()
    names = sorted({x for games in by_map.values() for g in games for x in g["b"] + g["r"]})
    index = {n: i for i, n in enumerate(names)}
    maps = {}
    for map_name, games in sorted(by_map.items()):
        if len(games) < MIN_MAP_GAMES:
            continue
        maps[map_name] = {"mode": modes.get(map_name, ""), **analyze_map(games, names, index)}
        print(f"{map_name}: {len(games)}판, 로그손실 {maps[map_name]['fit']['logloss']}")

    meta_path = STORE / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "window_days": KEEP_DAYS,
        "demo": bool(meta.get("demo", False)),
        "total_games": sum(m["games"] for m in maps.values()),
        "brawlers": names,
        "params": {"lambda_beta": LAMBDA_BETA, "lambda_pair": LAMBDA_PAIR,
                   "min_pair_games": MIN_PAIR_GAMES, "pair_z": PAIR_Z},
        "maps": maps,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"저장: {OUT} ({OUT.stat().st_size / 1024:.0f} KB, 맵 {len(maps)}개, 총 {out['total_games']}판)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
