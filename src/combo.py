"""보조지표를 여러 개 겹치면 나아지는지 과거로 돌려 센다.

네 가지 지표를 먼저 정해 놓고(결과를 보기 전에), 켜진 지표의 개수별로 3년 뒤 커진 비율을 센다.
  내용  양산 문제를 다룬 제목의 비중이 10%p 넘게 올랐다
  사람  최근 3년 발표 기관의 70% 이상이 새로 들어온 곳이다
  흐름  발표 비중이 이미 오르는 중이다 (최근 3년이 그 전의 1.2배 이상)
  전파  최근 3년에 이 주제를 발표한 학회 수가 그 전보다 늘었다 (패키징 학회 다섯 곳 기준)
대상: 기준 시점에 이미 활발한 주제(비중 상위 25%), 일반 낱말 제외.

사용: python src/combo.py ectc pkg
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import analyze

ROOT = Path(__file__).resolve().parent.parent
VENUES = ["ectc", "eptc", "icept", "estc", "itherm"]
NAMES = ["내용", "사람", "흐름", "전파"]


def venue_years():
    """주제 → 학회 → 발표가 있었던 해 집합."""
    out = defaultdict(lambda: defaultdict(set))
    for v in VENUES:
        f = ROOT / "data" / "raw" / f"{v}.jsonl"
        if not f.exists():
            continue
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            if r["year"]:
                for t in analyze.terms(r["title"]):
                    out[t][v].add(r["year"])
    return out


def signals(data, t, topic, c, vy):
    yrs, tot = data["years"], data["total"]

    def sh(a, b):
        idx = [i for i, y in enumerate(yrs) if a <= y <= b]
        s = sum(tot[i] for i in idx)
        return sum(t["count"][i] for i in idx) / s if s else 0.0

    j = t["judge"].get(str(c)) or {}
    old = {n for p in t["papers"] if p["y"] <= c - 3 for n in p["inst"]}
    new = {n for p in t["papers"] if c - 2 <= p["y"] <= c for n in p["inst"]}
    before, recent = sh(yrs[0], c - 3), sh(c - 2, c)
    v_recent = sum(1 for v, ys in vy[topic].items() if any(c - 2 <= y <= c for y in ys))
    v_before = sum(1 for v, ys in vy[topic].items() if any(y <= c - 3 for y in ys))
    return [bool(j.get("lit")), bool(new) and len(new - old) / len(new) >= 0.7,
            before > 0 and recent / before >= 1.2, v_recent > v_before]


def run(key, labels, vy):
    data = json.loads((ROOT / "data" / "derived" / f"{key}.json").read_text(encoding="utf-8"))
    keep = lambda n: labels.get(n, {"keep": True}).get("keep", True)
    rows = []
    for b in data["backtest"]:
        hot = [(x["topic"], x["grew"]) for x in b["lit_topics"] if x["hot"]] + [(x["topic"], x["grew"]) for x in b["unlit_hot"]]
        for topic, grew in hot:
            if keep(topic) and topic in data["topics"]:
                rows.append({"c": b["cutoff"], "topic": topic, "grew": grew, "s": signals(data, data["topics"][topic], topic, b["cutoff"], vy)})
    res = {"venue": key, "n": len(rows), "grew": sum(r["grew"] for r in rows), "by_count": [], "by_signal": [], "by_cutoff": []}
    for k in range(5):
        g = [r for r in rows if sum(r["s"]) == k]
        res["by_count"].append({"signals_on": k, "topics": len(g), "grew": sum(r["grew"] for r in g)})
    for i, nm in enumerate(NAMES):
        on, off = [r for r in rows if r["s"][i]], [r for r in rows if not r["s"][i]]
        res["by_signal"].append({"signal": nm, "on": [sum(r["grew"] for r in on), len(on)], "off": [sum(r["grew"] for r in off), len(off)]})
    for c in sorted({r["c"] for r in rows}):
        for lo, hi, lab in ((0, 1, "0~1개"), (2, 4, "2개 이상")):
            g = [r for r in rows if r["c"] == c and lo <= sum(r["s"]) <= hi]
            res["by_cutoff"].append({"cutoff": c, "group": lab, "topics": len(g), "grew": sum(r["grew"] for r in g)})
    return res


if __name__ == "__main__":
    labels = json.loads((ROOT / "data" / "derived" / "topic_labels.json").read_text(encoding="utf-8"))
    vy = venue_years()
    allres = []
    for key in sys.argv[1:] or ["ectc", "pkg"]:
        r = run(key, labels, vy)
        allres.append(r)
        pct = lambda a, b: f"{a}/{b} ({a/b:.0%})" if b else "0/0"
        print(f"== {key}: 활발한 주제 {r['n']}개(세 시점 합), 커진 것 {r['grew']}개")
        for x in r["by_count"]:
            print(f"   켜진 지표 {x['signals_on']}개: {pct(x['grew'], x['topics'])}")
        for x in r["by_signal"]:
            print(f"   {x['signal']}: 켜짐 {pct(*x['on'])} | 꺼짐 {pct(*x['off'])}")
        for x in r["by_cutoff"]:
            print(f"   {x['cutoff']}년 · {x['group']}: {pct(x['grew'], x['topics'])}")
    (ROOT / "data" / "derived" / "combo.json").write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")
