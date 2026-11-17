#!/usr/bin/env python3
"""랭크전 전투 기록 수집기 (Brawl Stars 공식 API).

동작 순서
1. 여러 나라의 랭킹 상위 플레이어 태그를 시작점으로 삼는다.
2. 각 플레이어의 최근 전투 기록(최대 25판)을 받아 랭크전 3대3만 골라낸다.
3. 전투에 등장한 다른 플레이어 태그를 대기열에 넣어 계속 넓혀 간다(눈덩이 방식).
4. 같은 판이 여러 플레이어 기록에 중복으로 나오므로 해시 키로 중복을 제거한다.

저장 형식: STORE_DIR/games/YYYY-MM-DD.jsonl.gz (한 줄 = 한 판)
  {"k": 해시키, "t": 전투시각, "m": 맵, "md": 모드, "b": [3명], "r": [3명], "w": 1|0}
  w = 1 이면 "b" 팀 승리. 플레이어 태그는 저장하지 않는다(해시 키에만 일방향으로 사용).
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

API_BASE = os.environ.get("BS_API_BASE", "https://bsproxy.royaleapi.dev/v1").rstrip("/")
TOKEN = os.environ.get("BS_API_TOKEN", "")
STORE = Path(os.environ.get("STORE_DIR", "store"))
MAX_REQUESTS = int(os.environ.get("MAX_REQUESTS", "3000"))
TIME_BUDGET = float(os.environ.get("TIME_BUDGET_SEC", "900"))
WORKERS = int(os.environ.get("WORKERS", "4"))
COUNTRIES = [c.strip() for c in os.environ.get(
    "SEED_COUNTRIES", "global,KR,US,BR,FR,DE,JP,MX,ES,TR,ID,IT").split(",") if c.strip()]
RANKED_TYPES = {t.strip() for t in os.environ.get("RANKED_TYPES", "soloRanked,teamRanked").split(",")}
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
# 전투 기록에는 며칠 전 판도 섞여 나온다. 중복 제거를 확실히 하려고 최근 DEDUP_DAYS 일 판만 받는다.
DEDUP_DAYS = int(os.environ.get("DEDUP_DAYS", "3"))


class FatalApiError(RuntimeError):
    pass


_lock = threading.Lock()
_requests_made = 0
_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})


def api_get(path: str):
    """GET 요청. 429/5xx는 재시도, 404는 None, 403은 키 문제로 보고 중단."""
    global _requests_made
    for attempt in range(5):
        with _lock:
            if _requests_made >= MAX_REQUESTS:
                return None
            _requests_made += 1
        try:
            resp = _session.get(API_BASE + path, timeout=20)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        if resp.status_code == 403:
            raise FatalApiError(
                "403: API 키가 거부되었습니다. 키에 등록한 IP가 프록시 IP와 같은지, "
                "BS_API_TOKEN 비밀값이 맞는지 확인하세요. 응답: " + resp.text[:200])
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2.0 * (attempt + 1))
            continue
        return None
    return None


def tag_path(tag: str) -> str:
    return quote("#" + tag.lstrip("#").upper(), safe="")


def parse_time(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_battle(item: dict, owner_tag: str):
    """전투 기록 1건 → (게임 레코드 또는 None, 발견한 태그 목록)."""
    battle = item.get("battle") or {}
    event = item.get("event") or {}
    teams = battle.get("teams")
    found = []
    if isinstance(teams, list):
        for team in teams:
            for p in team or []:
                if p.get("tag"):
                    found.append(p["tag"].lstrip("#").upper())
    if battle.get("type") not in RANKED_TYPES:
        return None, found
    if not isinstance(teams, list) or len(teams) != 2 or any(len(t or []) != 3 for t in teams):
        return None, found
    map_name = event.get("map")
    result = battle.get("result")
    if not map_name or result not in ("victory", "defeat"):
        return None, found  # 무승부·정보 누락 판은 제외

    owner = "#" + owner_tag.lstrip("#").upper()
    owner_idx = next((i for i, t in enumerate(teams) if any(p.get("tag") == owner for p in t)), None)
    if owner_idx is None:
        return None, found
    winner_idx = owner_idx if result == "victory" else 1 - owner_idx

    try:
        sides = []
        for t in teams:
            tags = sorted(p["tag"] for p in t)
            names = sorted(p["brawler"]["name"].upper() for p in t)
            sides.append((",".join(tags), names))
    except (KeyError, TypeError, AttributeError):
        return None, found
    # 어느 플레이어 기록에서 봐도 같은 순서가 되도록 팀 순서를 태그 기준으로 고정한다.
    order = sorted(range(2), key=lambda i: sides[i][0])
    first, second = order
    t = item.get("battleTime", "")
    key = hashlib.sha1(f"{t}|{sides[first][0]}|{sides[second][0]}".encode()).hexdigest()[:16]
    rec = {
        "k": key, "t": t, "m": map_name,
        "md": event.get("mode") or battle.get("mode") or "",
        "b": sides[first][1], "r": sides[second][1],
        "w": 1 if winner_idx == first else 0,
    }
    return rec, found


def day_file(day: dt.date) -> Path:
    return STORE / "games" / f"{day.isoformat()}.jsonl.gz"


def load_recent_keys(today: dt.date) -> set[str]:
    keys = set()
    for d in range(DEDUP_DAYS + 1):
        f = day_file(today - dt.timedelta(days=d))
        if f.exists():
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        keys.add(json.loads(line)["k"])
                    except (ValueError, KeyError):
                        pass
    return keys


def prune_old(today: dt.date):
    cutoff = today - dt.timedelta(days=KEEP_DAYS)
    for f in (STORE / "games").glob("*.jsonl.gz"):
        try:
            if dt.date.fromisoformat(f.name[:10]) < cutoff:
                f.unlink()
        except ValueError:
            pass


def main() -> int:
    if not TOKEN:
        print("BS_API_TOKEN 이 설정되지 않았습니다.", file=sys.stderr)
        return 2
    start = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    (STORE / "games").mkdir(parents=True, exist_ok=True)
    seen_keys = load_recent_keys(today)
    min_time = now - dt.timedelta(days=DEDUP_DAYS)

    queue: deque[str] = deque()
    visited: set[str] = set()
    try:
        for country in COUNTRIES:
            data = api_get(f"/rankings/{country}/players?limit=200")
            for p in (data or {}).get("items", []):
                tag = p.get("tag", "").lstrip("#").upper()
                if tag and tag not in visited:
                    visited.add(tag)
                    queue.append(tag)
    except FatalApiError as e:
        print(e, file=sys.stderr)
        return 3
    print(f"시작 플레이어 {len(queue)}명 (나라 {len(COUNTRIES)}곳)")

    new_by_day: dict[dt.date, list[dict]] = {}
    stats = {"battlelogs": 0, "ranked_seen": 0, "new_games": 0, "duplicates": 0}

    def fetch(tag):
        return tag, api_get(f"/players/{tag_path(tag)}/battlelog")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while queue and time.time() - start < TIME_BUDGET and _requests_made < MAX_REQUESTS:
            batch = [queue.popleft() for _ in range(min(len(queue), WORKERS * 4))]
            try:
                results = list(pool.map(fetch, batch))
            except FatalApiError as e:
                print(e, file=sys.stderr)
                return 3
            for owner, data in results:
                if not data:
                    continue
                stats["battlelogs"] += 1
                for item in data.get("items", []):
                    rec, found = parse_battle(item, owner)
                    for tag in found:
                        if tag not in visited:
                            visited.add(tag)
                            queue.append(tag)
                    if rec is None:
                        continue
                    stats["ranked_seen"] += 1
                    t = parse_time(rec["t"])
                    if t is None or t < min_time:
                        continue
                    if rec["k"] in seen_keys:
                        stats["duplicates"] += 1
                        continue
                    seen_keys.add(rec["k"])
                    new_by_day.setdefault(t.date(), []).append(rec)
                    stats["new_games"] += 1

    for day, recs in new_by_day.items():
        with gzip.open(day_file(day), "at", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    prune_old(today)

    stats.update({"requests": _requests_made, "seconds": round(time.time() - start, 1),
                  "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    (STORE / "last_run.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
