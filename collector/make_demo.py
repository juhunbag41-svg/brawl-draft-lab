#!/usr/bin/env python3
"""검증·미리보기용 가짜 게임 데이터 생성기.

정답 파라미터(브롤러 강도 β, 시너지 γ, 카운터 δ)를 정해 놓고 그 모델대로 게임 결과를 뽑는다.
aggregate.py 가 이 정답을 얼마나 되찾는지 비교하면 추정 방법을 검증할 수 있다.
생성된 통계는 실제 게임 데이터가 아니므로 사이트에 '샘플 데이터'로 표시된다.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import sys
from pathlib import Path

import numpy as np

STORE = Path(os.environ.get("STORE_DIR", "store"))
GAMES_PER_MAP = int(os.environ.get("DEMO_GAMES_PER_MAP", "40000"))
SEED = int(os.environ.get("DEMO_SEED", "7"))

NAMES = ["SHELLY", "COLT", "BULL", "BROCK", "RICO", "SPIKE", "BARLEY", "JESSIE", "NITA", "DYNAMIKE",
         "EL PRIMO", "MORTIS", "CROW", "POCO", "BO", "PIPER", "PAM", "TARA", "DARRYL", "PENNY", "FRANK",
         "GENE", "TICK", "LEON", "ROSA", "CARL", "BIBI", "8-BIT", "SANDY", "BEA", "EMZ", "MR. P", "MAX",
         "JACKY", "GALE", "NANI", "SPROUT", "SURGE", "COLETTE", "AMBER", "LOU", "BYRON", "EDGAR", "RUFFS",
         "STU", "BELLE", "SQUEAK", "GROM", "BUZZ", "GRIFF", "ASH", "MEG", "LOLA", "FANG", "EVE", "JANET",
         "BONNIE", "OTIS", "SAM", "GUS", "BUSTER", "CHESTER", "GRAY", "MANDY", "R-T", "WILLOW", "MAISIE",
         "HANK", "CORDELIUS", "DOUG", "PEARL", "CHUCK", "CHARLIE", "MICO", "KIT", "MELODIE", "ANGELO",
         "DRACO", "LILY", "BERRY", "CLANCY", "MOE", "KENJI", "SHADE", "JUJU", "OLLIE", "LUMI", "FINX",
         "KAZE", "ALLI", "TRUNK", "MINA"]

MAPS = {
    "Hard Rock Mine": "gemGrab", "Undermine": "gemGrab", "Hideout": "bounty", "Layer Cake": "bounty",
    "Center Stage": "brawlBall", "Sneaky Fields": "brawlBall", "Out in the Open": "knockout",
    "Belle's Rock": "knockout", "Hot Potato": "heist", "Dueling Beetles": "hotZone",
}


def main() -> int:
    rng = np.random.default_rng(SEED)
    N = len(NAMES)
    today = dt.datetime.now(dt.timezone.utc).date()
    out_dir = STORE / "games"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.jsonl.gz"):
        f.unlink()
    truth = {}
    files = {}
    for map_name, mode in MAPS.items():
        beta = rng.normal(0, 0.25, N)
        popularity = np.exp(1.2 * beta + rng.normal(0, 0.9, N))
        popularity /= popularity.sum()
        syn = {}
        for _ in range(25):
            a, b = sorted(rng.choice(N, 2, replace=False))
            syn[(a, b)] = rng.normal(0, 0.35)
        ctr = {}
        for _ in range(40):
            a, b = rng.choice(N, 2, replace=False)
            lo, hi = min(a, b), max(a, b)
            v = rng.normal(0, 0.35)
            ctr[(lo, hi)] = v if a == lo else -v

        # 실제 랭크전처럼 시너지 좋은 짝이 더 자주 함께 뽑히도록, 쌍을 일부 강제로 넣는다.
        syn_pairs = list(syn.keys())
        for gi in range(GAMES_PER_MAP):
            picks = []
            if rng.random() < 0.3:
                picks.extend(syn_pairs[rng.integers(len(syn_pairs))])
            while len(picks) < 6:
                c = int(rng.choice(N, p=popularity))
                if c not in picks:
                    picks.append(c)
            team_b, team_r = picks[:3], picks[3:]  # 강제로 넣은 시너지 쌍은 같은 팀(앞 3명)에 들어간다
            if rng.random() < 0.5:
                team_b, team_r = team_r, team_b
            z = beta[team_b].sum() - beta[team_r].sum()
            for T, s in ((team_b, 1), (team_r, -1)):
                for i in range(3):
                    for j in range(i + 1, 3):
                        z += s * syn.get((min(T[i], T[j]), max(T[i], T[j])), 0.0)
            for a in team_b:
                for c in team_r:
                    lo, hi = min(a, c), max(a, c)
                    v = ctr.get((lo, hi), 0.0)
                    z += v if a == lo else -v
            w = int(rng.random() < 1 / (1 + np.exp(-z)))
            day = today - dt.timedelta(days=int(rng.integers(0, 14)))
            rec = {"k": f"demo{map_name[:3]}{gi:07d}", "t": day.strftime("%Y%m%dT120000.000Z"),
                   "m": map_name, "md": mode,
                   "b": sorted(NAMES[i] for i in team_b), "r": sorted(NAMES[i] for i in team_r), "w": w}
            files.setdefault(day, []).append(rec)
        truth[map_name] = {
            "beta": {NAMES[i]: float(beta[i]) for i in range(N)},
            "syn": {f"{NAMES[a]}|{NAMES[b]}": float(v) for (a, b), v in syn.items()},
            "ctr": {f"{NAMES[a]}|{NAMES[b]}": float(v) for (a, b), v in ctr.items()},
        }
    for day, recs in files.items():
        with gzip.open(out_dir / f"{day.isoformat()}.jsonl.gz", "wt", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    (STORE / "meta.json").write_text(json.dumps({"demo": True}))
    (STORE / "demo_truth.json").write_text(json.dumps(truth, ensure_ascii=False))
    print(f"가짜 게임 {GAMES_PER_MAP * len(MAPS)}판 생성 (맵 {len(MAPS)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
