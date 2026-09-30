"""수집한 발표 기록으로 네 가지를 계산한다.

  읽는다   주제별 연도 추이 + 내용 신호(양산 문제 단어가 들어간 제목의 비중)
  찾는다   주제별 기관 · 저자
  검증한다 기준 연도를 과거로 돌려 신호가 맞았는지 센다
(대조한다 = 산업 사건은 data/events.json 에서 읽어 화면에서 같은 시간축에 놓는다)

사용: python src/analyze.py ectc
결과: data/derived/<학회>.json
모든 숫자는 이 코드가 센다. LLM은 숫자를 만들지 않는다.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST, LAST = 2018, 2026

# 양산 단계에서 나오는 문제를 가리키는 단어 (제목 기준)
PROBLEM = re.compile(
    r"reliab|failure|fatigue|stress|warpage|crack|void|delaminat|defect|degrad|lifetime|yield|thermal cycl|"
    r"electromigration|moisture|drop test|aging", re.I)

STOP = set("""a an the of for and in on with to by using via from at as is are be its their into over under between
based new novel high low ultra advanced study analysis investigation development evaluation characterization
design process method approach application applications technology technologies performance effect effects
toward towards enabled enabling next generation future first large small fine improved enhanced through during
highly different various two three multi single use used can that this it or we our""".split())

MIN_DOCS = 10        # 기준 연도까지 이만큼은 있어야 주제로 본다
MIN_RECENT = 5       # 최근 3년에 이만큼은 있어야 신호를 계산한다
SIGNAL_RISE = 0.10   # 문제 단어 비중이 이만큼(10%p) 오르면 신호가 켜졌다고 본다
GROW = 1.5           # 3년 뒤 비중이 이 배수 이상이면 "커졌다"
HOT_TOP = 0.25       # 기준 시점에 비중 상위 25%인 주제 = 이미 활발한 주제


def words(title):
    # 하이픈은 띄어쓰기로 본다 (glass-core → glass, core)
    return [w for w in re.findall(r"[a-z][a-z0-9]+", title.lower().replace("-", " ")) if len(w) > 2]


def terms(title):
    ws = words(title)
    out = {w for w in ws if w not in STOP}
    out |= {f"{a} {b}" for a, b in zip(ws, ws[1:]) if a not in STOP and b not in STOP}
    return out


def load(key):
    return [json.loads(l) for l in open(ROOT / "data" / "raw" / f"{key}.jsonl", encoding="utf-8")]


def build(key):
    rows = [r for r in load(key) if r["year"] and FIRST <= r["year"] <= LAST]
    years = list(range(FIRST, LAST + 1))
    total = Counter(r["year"] for r in rows)
    by_term = defaultdict(list)
    for i, r in enumerate(rows):
        r["problem"] = bool(PROBLEM.search(r["title"]))
        for t in terms(r["title"]):
            by_term[t].append(i)

    def cnt(idx, y0, y1, only_problem=False):
        return sum(1 for i in idx if y0 <= rows[i]["year"] <= y1 and (rows[i]["problem"] or not only_problem))

    def share(idx, y0, y1):
        tot = sum(total[y] for y in range(y0, y1 + 1))
        return cnt(idx, y0, y1) / tot if tot else 0.0

    def judge(idx, c):
        """기준 연도 c까지의 기록만 보고 신호를 계산한다."""
        if cnt(idx, FIRST, c) < MIN_DOCS:
            return None
        recent, before = cnt(idx, c - 2, c), cnt(idx, FIRST, c - 3)
        if recent < MIN_RECENT or before == 0:
            return {"eligible": True, "lit": False, "reason": "최근 또는 이전 기록이 적다"}
        p_recent = cnt(idx, c - 2, c, True) / recent
        p_before = cnt(idx, FIRST, c - 3, True) / before
        return {"eligible": True, "lit": (p_recent - p_before) >= SIGNAL_RISE,
                "p_before": round(p_before, 3), "p_recent": round(p_recent, 3),
                "n_before": before, "n_recent": recent,
                "k_before": cnt(idx, FIRST, c - 3, True), "k_recent": cnt(idx, c - 2, c, True)}

    def outcome(idx, c):
        """가려 둔 c+1 ~ c+3년을 열어 실제로 커졌는지 본다."""
        a, b = share(idx, c - 2, c), share(idx, c + 1, c + 3)
        return {"share_then": round(a, 4), "share_after": round(b, 4), "grew": bool(a > 0 and b >= GROW * a)}

    # ── 검증: 과거 세 시점
    backtest = []
    for c in (2021, 2022, 2023):
        lit, unlit = [], []
        cand = [(t, idx, judge(idx, c)) for t, idx in by_term.items()]
        cand = [(t, idx, j) for t, idx, j in cand if j]
        ranked = sorted(cand, key=lambda x: -share(x[1], c - 2, c))
        hot = {t for t, _, _ in ranked[:int(len(ranked) * HOT_TOP)]}
        for t, idx, j in cand:
            o = outcome(idx, c)
            j["hot"] = t in hot
            (lit if j["lit"] else unlit).append({"topic": t, **j, **o})
        lit.sort(key=lambda x: -x["share_after"])
        backtest.append({
            "cutoff": c, "opened": f"{c+1}~{c+3}", "topics": len(lit) + len(unlit),
            "lit": len(lit), "lit_grew": sum(x["grew"] for x in lit),
            "unlit": len(unlit), "unlit_grew": sum(x["grew"] for x in unlit),
            "hot_lit": sum(x["hot"] for x in lit), "hot_lit_grew": sum(x["hot"] and x["grew"] for x in lit),
            "hot_unlit": sum(x["hot"] for x in unlit), "hot_unlit_grew": sum(x["hot"] and x["grew"] for x in unlit),
            "lit_topics": [{k: x[k] for k in ("topic", "grew", "hot", "share_then", "share_after", "p_before", "p_recent")} for x in lit],
        })

    # ── 주제별 상세 (오늘 기준으로 주제가 되는 것만)
    topics = {}
    for t, idx in by_term.items():
        if len(idx) < MIN_DOCS:
            continue
        inst, auth = Counter(), Counter()
        inst_meta, auth_name = {}, {}
        for i in idx:
            seen = set()
            for a in rows[i]["authors"]:
                if a["id"]:
                    auth[a["id"]] += 1
                    auth_name[a["id"]] = a["name"]
                for ins in a["inst"]:
                    if ins["id"] and ins["id"] not in seen:
                        seen.add(ins["id"])
                        inst[ins["id"]] += 1
                        inst_meta[ins["id"]] = ins
        topics[t] = {
            "n": len(idx),
            "count": [cnt(idx, y, y) for y in years],
            "share": [round(cnt(idx, y, y) / total[y], 4) if total[y] else 0 for y in years],
            "problem": [cnt(idx, y, y, True) for y in years],
            "judge": {str(c): judge(idx, c) for c in range(2020, LAST + 1)},
            "n_inst": len(inst), "n_auth": len(auth),
            "inst": [{"name": inst_meta[k]["name"], "country": inst_meta[k]["country"], "type": inst_meta[k]["type"], "n": n}
                     for k, n in inst.most_common(12)],
            "auth": [{"name": auth_name[k], "n": n} for k, n in auth.most_common(8)],
            "papers": [{"y": rows[i]["year"], "t": rows[i]["title"], "doi": rows[i]["doi"], "p": rows[i]["problem"],
                        "inst": sorted({ins["name"] for a in rows[i]["authors"] for ins in a["inst"] if ins["name"]}),
                        "auth": [a["name"] for a in rows[i]["authors"] if a["name"]]}
                       for i in sorted(idx, key=lambda i: (rows[i]["year"], rows[i]["title"]))],
        }

    # ── 기관 · 저자별 발표 흐름 (누가 어디로 옮겨 갔나)
    topic_names = set(topics)

    def flow(kind):
        bucket = defaultdict(list)
        for i, r in enumerate(rows):
            names = ({a["name"] for a in r["authors"] if a["name"]} if kind == "auth"
                     else {ins["name"] for a in r["authors"] for ins in a["inst"] if ins["name"]})
            for n in names:
                bucket[n].append(i)
        out = {}
        for n, idx in bucket.items():
            if len(idx) < (5 if kind == "auth" else 8):
                continue
            by_year = {}
            for y in years:
                ys = [i for i in idx if rows[i]["year"] == y]
                if not ys:
                    continue
                tc = Counter(t for i in ys for t in terms(rows[i]["title"]) if t in topic_names and " " not in t)
                by_year[str(y)] = {"n": len(ys), "terms": [t for t, _ in tc.most_common(5)],
                                   "papers": [{"t": rows[i]["title"], "doi": rows[i]["doi"]} for i in ys]}
            out[n] = {"n": len(idx), "years": by_year}
        return out

    who = {"auth": flow("auth"), "inst": flow("inst")}
    all_inst = {ins["id"] for r in rows for a in r["authors"] for ins in a["inst"] if ins["id"]}
    all_auth = {a["id"] for r in rows for a in r["authors"] if a["id"]}
    meta = json.loads((ROOT / "data" / "raw" / f"{key}.meta.json").read_text(encoding="utf-8"))
    out = {"meta": meta, "years": years, "total": [total[y] for y in years],
           "n_papers": len(rows), "n_inst": len(all_inst), "n_auth": len(all_auth), "n_topics": len(topics),
           "rules": {"min_docs": MIN_DOCS, "min_recent": MIN_RECENT, "signal_rise": SIGNAL_RISE, "grow": GROW,
                     "problem_words": PROBLEM.pattern},
           "backtest": backtest, "topics": topics, "who": who}
    d = ROOT / "data" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    for k in sys.argv[1:] or ["ectc"]:
        o = build(k)
        print(k, "논문", o["n_papers"], "기관", o["n_inst"], "저자", o["n_auth"], "주제", o["n_topics"])
        for b in o["backtest"]:
            lr = b["lit_grew"] / b["lit"] if b["lit"] else 0
            ur = b["unlit_grew"] / b["unlit"] if b["unlit"] else 0
            print(f"  기준 {b['cutoff']}: 주제 {b['topics']} | 신호 켜짐 {b['lit']} 중 {b['lit_grew']} 커짐 ({lr:.0%}) | 안 켜짐 {b['unlit']} 중 {b['unlit_grew']} ({ur:.0%})")
            h = b["hot_lit_grew"] / b["hot_lit"] if b["hot_lit"] else 0
            hu = b["hot_unlit_grew"] / b["hot_unlit"] if b["hot_unlit"] else 0
            print(f"     활발한 주제만: 켜짐 {b['hot_lit']} 중 {b['hot_lit_grew']} ({h:.0%}) | 안 켜짐 {b['hot_unlit']} 중 {b['hot_unlit_grew']} ({hu:.0%})")
            print("     켜진 활발한 주제:", ", ".join(("✓" if x["grew"] else "✗") + x["topic"] for x in b["lit_topics"] if x["hot"]))
