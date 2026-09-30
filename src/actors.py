"""기관 · 연구원에서 출발하는 보기.

주제가 아니라 사람과 기관을 줄 세운다. 세는 것은 셋:
  - 요즘 들어간 후보 주제: 지금 후보 주제 가운데, 이 기관(연구원)의 첫 발표가 최근 3년에 있는 것
  - 연속 발표: 마지막 해부터 거꾸로 끊기지 않고 발표한 햇수
  - 그때 다룬 뜨는 주제의 결과: 2023년 기준 활발한 주제 가운데 2021~2023년에 발표한 것이 3년 뒤 성장했나
세 번째는 한 시점 · 작은 표본의 기록일 뿐, 앞으로를 맞히는지 시험하지 않았다 (화면에 '검증 전'으로 표시).
"""
import json
from collections import defaultdict
from pathlib import Path

import analyze

ROOT = Path(__file__).resolve().parent.parent


def records(key):
    members = analyze.GROUPS[key]["members"] if key in analyze.GROUPS else [key]
    out, seen = [], set()
    for m in members:
        for line in open(ROOT / "data" / "raw" / f"{m}.jsonl", encoding="utf-8"):
            r = json.loads(line)
            if not r["year"] or r["id"] in seen:
                continue
            seen.add(r["id"])
            insts = {(i["name"], i["type"], i.get("country") or "") for a in r["authors"] for i in a["inst"] if i["name"]}
            auths = {(a["name"], (a["inst"][0]["name"] if a["inst"] else "")) for a in r["authors"] if a.get("name")}
            out.append((r["year"], set(analyze.terms(r["title"])), insts, auths))
    return out


def build(key, cand, ko, hot2023, last, kind):
    """cand: 지금 후보 주제 · ko: 주제 한글 이름 · hot2023: {주제: 성장했나} (2023년 기준 활발한 주제)."""
    recs = records(key)
    cand = set(cand)
    years, first, lastt, recent, early, meta = defaultdict(set), defaultdict(dict), defaultdict(dict), defaultdict(int), defaultdict(set), {}
    for y, ts, insts, auths in recs:
        who = ([(n, t) for n, t, _ in insts] if kind == "inst" else list({(co, "") for _, _, co in insts if co}) if kind == "country" else list(auths))
        for n, extra in who:
            meta.setdefault(n, extra)
            years[n].add(y)
            if last - 2 <= y <= last:
                recent[n] += 1
            for t in ts:
                if t in cand:
                    first[n][t] = min(y, first[n].get(t, y))
                    lastt[n][t] = max(y, lastt[n].get(t, y))
                if t in hot2023 and 2021 <= y <= 2023:
                    early[n].add(t)
    rows = []
    for n, k in recent.items():
        if k < (3 if kind == "auth" else 5):
            continue
        streak, y = 0, last
        while y in years[n]:
            streak, y = streak + 1, y - 1
        now = sorted([t for t, fy in first[n].items()], key=lambda t: first[n][t])
        rec_topics = [t for t in now if lastt[n][t] >= last - 2]
        e = sorted(early[n])
        rows.append({"name": n, "sub": meta.get(n) or "", "recent": k, "streak": streak, "since": min(years[n]),
                     "topics": [{"topic": t, "ko": ko.get(t) or t, "new": first[n][t] >= last - 2} for t in rec_topics],
                     "entered": sum(1 for t in rec_topics if first[n][t] >= last - 2),
                     "track": {"n": len(e), "grew": sum(1 for t in e if hot2023[t]),
                               "won": [ko.get(t) or t for t in e if hot2023[t]][:6]}})
    rows.sort(key=lambda r: -r["recent"])
    base = sum(1 for g in hot2023.values() if g) / max(1, len(hot2023))
    pick = lambda f, key_, n=5: [r for r in sorted(rows, key=key_) if f(r)][:n]
    return {"kind": kind, "last": last, "rows": rows[:40], "n": len(rows), "base_rate": round(base, 3), "hot_n": len(hot2023),
            "entered": pick(lambda r: r["entered"] > 0, lambda r: (-r["entered"], -r["recent"])),
            "track": pick(lambda r: r["track"]["n"] >= 3, lambda r: (-r["track"]["grew"] / max(1, r["track"]["n"]), -r["track"]["n"])),
            "streak": pick(lambda r: True, lambda r: (-r["streak"], -r["recent"]))}
