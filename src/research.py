"""지표를 더 늘리면 나아지는지 과거로 돌려 확인한다 (연구용, 화면은 건드리지 않는다).

기존 네 가지(내용 · 사람 · 흐름 · 전파)에 여섯 가지를 더해 본다. 정의는 결과를 보기 전에 적었다.
  기업  최근 3년 발표 중 기업 소속이 낀 발표의 비중이 그 전보다 10%p 넘게 올랐다
  국가  이 주제를 발표하는 나라 수가 그 전보다 늘었다
  가속  기준 연도의 발표 비중이 직전 2년 평균의 1.3배 이상이다
  협업  기관 둘 이상이 함께 쓴 발표의 비중이 그 전보다 10%p 넘게 올랐다
  분산  가장 많이 발표한 기관 한 곳의 몫이 그 전보다 5%p 넘게 줄었다 (한 곳의 주제에서 여러 곳의 주제로)
  분화  이 주제와 함께 나오는 서로 다른 주제어의 수가 그 전의 1.2배 이상이다
쓰지 않은 것: 인용 수 — 공개 기록의 인용 수는 오늘까지 쌓인 값이라, 과거 시점 검증에 넣으면 미래를 미리 보는 셈이 된다.

순서: ECTC에서 지표마다 '켜짐 대 꺼짐'을 세어 쓸 지표를 고른다 → ECTC를 뺀 네 학회(pkg4)에서 그대로 확인한다.
사용: python src/research.py
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

import analyze
import combo

ROOT = Path(__file__).resolve().parent.parent
NEW = ["기업", "국가", "가속", "협업", "분산", "분화"]
ALL = combo.NAMES + NEW


def raw_index(key):
    members = analyze.GROUPS[key]["members"] if key in analyze.GROUPS else [key]
    rows, seen = [], set()
    for m in members:
        for line in open(ROOT / "data" / "raw" / f"{m}.jsonl", encoding="utf-8"):
            r = json.loads(line)
            if r["year"] and r["id"] not in seen:
                seen.add(r["id"])
                insts = {(i["name"], i["type"], i["country"]) for a in r["authors"] for i in a["inst"] if i["name"]}
                rows.append((r["year"], analyze.terms(r["title"]), insts))
    idx = defaultdict(list)
    for k, (_, ts, _) in enumerate(rows):
        for t in ts:
            idx[t].append(k)
    total = Counter(y for y, _, _ in rows)
    return rows, idx, total


def extra(rows, idx, total, topic, c):
    ps = [rows[k] for k in idx[topic]]
    rec = [p for p in ps if c - 2 <= p[0] <= c]
    bef = [p for p in ps if p[0] <= c - 3]
    if not rec or not bef:
        return [False] * len(NEW)
    frac = lambda g, f: sum(1 for p in g if f(p)) / len(g)
    company = lambda p: any(tp == "company" for _, tp, _ in p[2])
    multi = lambda p: len({n for n, _, _ in p[2]}) >= 2
    countries = lambda g: {co for p in g for _, _, co in p[2] if co}
    top1 = lambda g: max(Counter(n for p in g for n in {n for n, _, _ in p[2]}).values(), default=0) / len(g)
    sh = lambda y: sum(1 for p in ps if p[0] == y) / total[y] if total[y] else 0.0
    prev = (sh(c - 2) + sh(c - 1)) / 2
    words = set(topic.split())
    cot = lambda g: {t for p in g for t in p[1] if t != topic and not (set(t.split()) & words)}
    return [frac(rec, company) - frac(bef, company) >= 0.10,
            len(countries(rec)) > len(countries(bef)),
            prev > 0 and sh(c) >= 1.3 * prev,
            frac(rec, multi) - frac(bef, multi) >= 0.10,
            top1(rec) <= top1(bef) - 0.05,
            len(cot(rec)) >= 1.2 * len(cot(bef))]


def table(key, labels, vy):
    data = json.loads((ROOT / "data" / "derived" / f"{key}.json").read_text(encoding="utf-8"))
    rows, idx, total = raw_index(key)
    keep = lambda n: labels.get(n, {"keep": True}).get("keep", True)
    out = []
    for b in data["backtest"]:
        hot = [(x["topic"], x["grew"]) for x in b["lit_topics"] if x["hot"]] + [(x["topic"], x["grew"]) for x in b["unlit_hot"]]
        for topic, grew in hot:
            if keep(topic) and topic in data["topics"]:
                s = combo.signals(data, data["topics"][topic], topic, b["cutoff"], vy) + extra(rows, idx, total, topic, b["cutoff"])
                out.append({"c": b["cutoff"], "topic": topic, "grew": grew, "s": dict(zip(ALL, s))})
    return out


pct = lambda a, b: f"{a}/{b} ({a/b:.0%})" if b else "0/0"


def each(rows):
    res = {}
    for n in ALL:
        on, off = [r for r in rows if r["s"][n]], [r for r in rows if not r["s"][n]]
        res[n] = (sum(r["grew"] for r in on), len(on), sum(r["grew"] for r in off), len(off))
    return res


def curve(rows, names):
    res = []
    for k in range(len(names) + 1):
        g = [r for r in rows if sum(r["s"][n] for n in names) == k]
        res.append((k, sum(r["grew"] for r in g), len(g)))
    return res


def split(rows, names, thr):
    hi = [r for r in rows if sum(r["s"][n] for n in names) >= thr]
    lo = [r for r in rows if sum(r["s"][n] for n in names) < thr]
    by = {c: (sum(r["grew"] for r in hi if r["c"] == c), sum(1 for r in hi if r["c"] == c),
              sum(r["grew"] for r in lo if r["c"] == c), sum(1 for r in lo if r["c"] == c)) for c in sorted({r["c"] for r in rows})}
    return (sum(r["grew"] for r in hi), len(hi), sum(r["grew"] for r in lo), len(lo)), by


if __name__ == "__main__":
    labels = json.loads((ROOT / "data" / "derived" / "topic_labels.json").read_text(encoding="utf-8"))
    vy = combo.venue_years()
    T = {k: table(k, labels, vy) for k in ("ectc", "pkg4")}
    report = {}
    for k, rows in T.items():
        print(f"\n== {k}: 활발한 주제 {len(rows)}개, 커진 것 {sum(r['grew'] for r in rows)}개")
        e = each(rows)
        for n, (a, b, c, d) in e.items():
            print(f"   {n}: 켜짐 {pct(a, b)} | 꺼짐 {pct(c, d)}")
        report[k] = {"n": len(rows), "grew": sum(r["grew"] for r in rows), "each": e}
    # ECTC에서만 고른다: 켜졌을 때의 비율이 꺼졌을 때보다 1.3배 이상이고, 켜진 주제가 15개 이상인 지표
    e = report["ectc"]["each"]
    chosen = [n for n in ALL if e[n][1] >= 15 and e[n][3] and (e[n][0] / e[n][1]) >= 1.3 * (e[n][2] / e[n][3])]
    print("\nECTC에서 고른 지표:", chosen)
    for label, names in (("기존 4개", combo.NAMES), ("전체 10개", ALL), ("ECTC에서 고른 것", chosen)):
        for k, rows in T.items():
            print(f"\n-- {label} · {k}")
            for kk, g, n in curve(rows, names):
                if n:
                    print(f"   {kk}개: {pct(g, n)}")
            thr = max(2, round(len(names) / 2))
            tot, by = split(rows, names, thr)
            print(f"   {thr}개 이상 대 미만: {pct(tot[0], tot[1])} 대 {pct(tot[2], tot[3])}")
            print("   시점별:", " · ".join(f"{c}년 {pct(a, b)} 대 {pct(x, y)}" for c, (a, b, x, y) in by.items()))
            report.setdefault("sets", {}).setdefault(label, {})[k] = {"names": names, "curve": curve(rows, names), "threshold": thr, "split": tot, "by_cutoff": by}
    # ── 전파 확인: ECTC에서 잰 지표가, ECTC를 뺀 네 학회에서의 3년 뒤 성장을 가리키나
    P = json.loads((ROOT / "data" / "derived" / "pkg4.json").read_text(encoding="utf-8"))
    yrs, tot = P["years"], P["total"]

    def sh4(t, a, b):
        ix = [i for i, y in enumerate(yrs) if a <= y <= b]
        s = sum(tot[i] for i in ix)
        return sum(t["count"][i] for i in ix) / s if s else 0.0

    X = []
    for r in T["ectc"]:
        t = P["topics"].get(r["topic"])
        if t and sum(t["count"][i] for i, y in enumerate(yrs) if y <= r["c"]) >= 5:
            a, b = sh4(t, r["c"] - 2, r["c"]), sh4(t, r["c"] + 1, r["c"] + 3)
            X.append({**r, "grew_ectc": r["grew"], "grew": a > 0 and b >= 1.5 * a})
    print(f"\n== 전파: ECTC의 활발한 주제 {len(X)}개가 다른 네 학회에서 3년 뒤 커졌나 (커진 것 {sum(r['grew'] for r in X)}개)")
    report["cross"] = {}
    for label, names, thr in (("기존 4개", combo.NAMES, 2), ("ECTC에서 고른 것", chosen, max(2, round(len(chosen) / 2)))):
        tot_, by = split(X, names, thr)
        print(f"   {label} {thr}개 이상 대 미만: {pct(tot_[0], tot_[1])} 대 {pct(tot_[2], tot_[3])}")
        print("     시점별:", " · ".join(f"{c}년 {pct(a, b)} 대 {pct(x, y)}" for c, (a, b, x, y) in by.items()))
        report["cross"][label] = {"threshold": thr, "split": tot_, "by_cutoff": by}
    g, ng = [r for r in X if r["grew_ectc"]], [r for r in X if not r["grew_ectc"]]
    print(f"   ECTC에서 커진 주제가 네 학회에서도 커짐: {pct(sum(r['grew'] for r in g), len(g))} | ECTC에서 안 커진 주제: {pct(sum(r['grew'] for r in ng), len(ng))}")
    report["chosen"] = chosen
    (ROOT / "data" / "derived" / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
