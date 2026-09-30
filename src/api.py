"""화면이 부르는 주소들. 서버가 요청마다 이 파일을 다시 읽으므로, 고쳐도 서버를 다시 띄울 필요가 없다."""
import importlib
import json
from pathlib import Path

import agent
import combo
import draft
import gaps

ROOT = Path(__file__).resolve().parent.parent


def events():
    return json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))


def upstream(topic):
    """이 주제에 사람이 적어 둔 다른 분야 연결이 있으면 그 흐름을 붙인다."""
    try:
        links = json.loads((ROOT / "data" / "links.json").read_text(encoding="utf-8"))
        raw = json.loads((ROOT / "data" / "raw" / "upstream.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    for key, link in links.items():
        if not key.startswith("_") and topic in link["topics"]:
            return {"label": link["label"], "field": link["field"], "why": link["why"], "collected_at": raw["collected_at"],
                    "series": {q: raw["series"][q] for q in link["queries"] if q in raw["series"]}}
    return None


SPREAD_VENUES = ["ectc", "eptc", "icept", "estc", "itherm"]
SPREAD_LINE = 2.0  # 발표 비중이 이 %를 처음 넘은 해를 "자리 잡은 해"로 본다


def venue_titles(derived, v):
    """학회별 (연도, 제목의 주제어 집합). 한 번 읽으면 서버가 살아 있는 동안 들고 있는다."""
    import analyze
    cache = derived.__dict__.setdefault("_titles_v2", {})
    f = ROOT / "data" / "raw" / f"{v}.jsonl"
    if not f.exists():
        return None
    m = f.stat().st_mtime
    if v not in cache or cache[v][0] != m:
        rows = [json.loads(l) for l in open(f, encoding="utf-8")]
        meta = json.loads((ROOT / "data" / "raw" / f"{v}.meta.json").read_text(encoding="utf-8"))
        cache[v] = (m, meta["name"], [(r["year"], analyze.terms(r["title"]),
                                       [(a["name"], [i["name"] for i in a["inst"] if i["name"]]) for a in r["authors"] if a["name"]])
                                      for r in rows if r["year"]])
    return cache[v][1], cache[v][2]


def spread(topic, cutoff, derived):
    """전파: 같은 주제가 학회마다 언제 나타나 자리 잡았나. 학회를 합치지 않고 따로 센다."""
    years = list(range(2018, 2027))
    out = []
    for v in SPREAD_VENUES:
        got = venue_titles(derived, v)
        if not got:
            continue
        name, rows = got
        tot, n = {y: 0 for y in years}, {y: 0 for y in years}
        for y, ts, _ in rows:
            if y in tot:
                tot[y] += 1
                n[y] += topic in ts
        share = [round(n[y] / tot[y] * 100, 1) if tot[y] and y <= cutoff else None for y in years]
        seen = [y for y in years if y <= cutoff and n[y]]
        out.append({"venue": v, "name": name, "papers": sum(n[y] for y in years if y <= cutoff), "total": sum(tot[y] for y in years if y <= cutoff),
                    "share": share, "first_year": seen[0] if seen else None,
                    "settled": next((y for y, s in zip(years, share) if s is not None and s >= SPREAD_LINE), None)})
    out.sort(key=lambda x: (x["settled"] or 9999, -x["papers"]))
    return {"topic": topic, "cutoff": cutoff, "years": years, "line": SPREAD_LINE, "venues": out}


def ontology(topic, cutoff, d, derived):
    """온톨로지 조회: 한 주제를 둘러싼 학회 — 기관 — 저자 — 세부 주제의 관계. 다섯 학회의 발표 기록에서 센다.

    학회—기관: 그 기관이 그 학회에서 이 주제로 발표한 편수
    기관—저자: 그 소속으로 이 주제를 발표한 편수
    저자—세부 주제: 그 저자의 발표 제목에 그 낱말이 함께 나온 편수
    기관—기관: 같은 발표에 함께 이름을 올린 편수(공동 발표)
    """
    from collections import Counter
    from itertools import combinations
    if topic not in d["topics"]:
        return {"error": "없는 주제"}
    labels = json.loads(agent.LABELS.read_text(encoding="utf-8")) if agent.LABELS.exists() else {}
    words = set(topic.split())
    inst, auth, co, ven = Counter(), Counter(), Counter(), Counter()
    e_vi, e_ia, e_at, e_ii = Counter(), Counter(), Counter(), Counter()
    n = 0
    for v in SPREAD_VENUES:
        got = venue_titles(derived, v)
        if not got:
            continue
        for y, ts, authors in got[1]:
            if y > cutoff or topic not in ts:
                continue
            n += 1
            V = v.upper()
            ven[V] += 1
            rel = [x for x in ts if x != topic and not (set(x.split()) & words) and labels.get(x, {"keep": False}).get("keep")]
            co.update(rel)
            insts = {i for _, ii in authors for i in ii}
            inst.update(insts)
            for i in insts:
                e_vi[(V, i)] += 1
            for a, b in combinations(sorted(insts), 2):
                e_ii[(a, b)] += 1
            for name, ii in authors:
                auth[name] += 1
                for i in ii:
                    e_ia[(i, name)] += 1
                for x in rel:
                    e_at[(name, x)] += 1
    top_i = [k for k, _ in inst.most_common(8)]
    top_t = [k for k, _ in co.most_common(8)]
    # 저자는 고른 기관에 속한 사람을 먼저
    in_top = [a for a, _ in auth.most_common(60) if any((i, a) in e_ia for i in top_i)]
    top_a = in_top[:10]
    ko = lambda x: labels.get(x, {}).get("ko") or x
    pick = lambda e, A, B: [{"a": a, "b": b, "n": c} for (a, b), c in e.items() if a in A and b in B]
    return {"topic": topic, "ko": labels.get(topic, {}).get("ko"), "papers": n, "cutoff": cutoff,
            "venues": [{"id": k, "n": c} for k, c in ven.most_common()],
            "inst": [{"id": k, "n": inst[k]} for k in top_i],
            "auth": [{"id": k, "n": auth[k]} for k in top_a],
            "topics": [{"id": k, "ko": ko(k), "n": co[k]} for k in top_t],
            "venue_inst": pick(e_vi, set(ven), set(top_i)), "inst_auth": pick(e_ia, set(top_i), set(top_a)),
            "auth_topic": pick(e_at, set(top_a), set(top_t)), "inst_inst": pick(e_ii, set(top_i), set(top_i)),
            "totals": {"inst": len(inst), "auth": len(auth)}}


def combo_result(venue):
    """지표를 겹친 개수별 과거 성적 (src/combo.py 가 계산해 둔 것)."""
    f = ROOT / "data" / "derived" / "combo.json"
    if not f.exists():
        return None
    return next((r for r in json.loads(f.read_text(encoding="utf-8")) if r["venue"] == venue), None)


def handle(path, q, derived):
    importlib.reload(agent)
    importlib.reload(gaps)
    importlib.reload(draft)
    d = derived(q.get("venue", "ectc"))
    if path == "/api/meta":
        top = sorted(((t, v["n"]) for t, v in d["topics"].items()), key=lambda x: -x[1])
        return {k: d[k] for k in ("meta", "years", "total", "n_papers", "n_inst", "n_auth", "n_topics", "rules", "backtest")} | {
            "topic_names": [t for t, _ in top], "score": agent.scorecard(d)}, 200
    if path == "/api/topic":
        name = q.get("q", "").strip().lower()
        if name not in d["topics"]:
            return {"error": f"'{name}' 주제가 없다 (발표 {d['rules']['min_docs']}편 미만이거나 제목에 없는 말)"}, 404
        lab = json.loads(agent.LABELS.read_text(encoding="utf-8")).get(name, {}) if agent.LABELS.exists() else {}
        return {"topic": name, "ko": lab.get("ko"), "desc": lab.get("desc"), **d["topics"][name], "events": events().get(name, []), "upstream": upstream(name)}, 200
    if path == "/api/who":
        kind, name = q.get("kind", "inst"), q.get("name", "")
        w = d.get("who", {}).get(kind, {}).get(name)
        return ({"kind": kind, "name": name, **w}, 200) if w else ({"error": "발표가 적어 흐름을 만들지 않았다"}, 404)
    if path == "/api/agent":
        c = int(q.get("c", 2026))
        return agent.run(q.get("q", ""), c, d, events(), upstream,
                         {"spread": lambda tp: spread(tp, c, derived), "relations": lambda tp: ontology(tp, c, d, derived)}), 200
    if path == "/api/gap":
        return gaps.gap_map(q.get("q", "").strip().lower(), int(q.get("c", 2023)), d), 200
    if path == "/api/find":
        c = int(q.get("c", 2026))
        cand = agent.candidates(d, c)
        labels = agent.label_topics([x["topic"] for x in cand])
        shown = [{**x, "ko": labels.get(x["topic"], {}).get("ko")} for x in cand if labels.get(x["topic"], {"keep": True}).get("keep", True)]
        bt = next((b for b in d["backtest"] if b["cutoff"] == c), None)
        grew = {x["topic"]: x["grew"] for x in bt["lit_topics"]} if bt else {}
        vy = derived.__dict__.get("_vy") or derived.__dict__.setdefault("_vy", combo.venue_years())
        for x in shown:
            s = combo.signals(d, d["topics"][x["topic"]], x["topic"], c, vy)
            x["signals"] = dict(zip(combo.NAMES, s))
            x["signals_on"] = sum(s)
        shown.sort(key=lambda x: (-x["active"], -x["signals_on"], -(x["problem_recent_percent"] - x["problem_before_percent"])))
        ev = events()
        desc = agent.describe_topics([x["topic"] for x in shown if x["active"]])
        for x in shown:
            x["desc"] = desc.get(x["topic"], {}).get("desc")
            x["grew"] = grew.get(x["topic"])
            x["events"] = len([e for e in ev.get(x["topic"], []) if int(e["date"][:4]) <= c])
            x["cross"] = bool(upstream(x["topic"]))
        # 가장 최근에 검증할 수 있는 시점(2023년)의 후보가 실제로 어떻게 됐는지 — 맞은 것과 틀린 것 모두
        last = d["backtest"][-1]
        lab_all = agent.label_topics([x["topic"] for x in last["lit_topics"]])
        past = [{"topic": x["topic"], "ko": lab_all.get(x["topic"], {}).get("ko") or x["topic"], "grew": x["grew"],
                 "then": round(x["share_then"] * 100, 1), "after": round(x["share_after"] * 100, 1)}
                for x in last["lit_topics"] if x["hot"] and lab_all.get(x["topic"], {"keep": True}).get("keep", True)]
        past.sort(key=lambda x: (-x["grew"], -x["after"]))
        return {"past": {"cutoff": last["cutoff"], "opened": last["opened"], "items": past}, "cutoff": c, "candidates": shown, "removed": len(cand) - len(shown), "labeled": bool(labels), "traits": agent.traits(d), "n_topics": d["n_topics"], "n_eligible": len(cand), "combo": combo_result(q.get("venue", "ectc"))}, 200
    if path == "/api/cross":
        links = json.loads((ROOT / "data" / "links.json").read_text(encoding="utf-8"))
        raw = json.loads((ROOT / "data" / "raw" / "upstream.json").read_text(encoding="utf-8"))
        link = links[q.get("key", "ai_scale")]
        labels = agent.label_topics(link["topics"])

        def doubled(vals, years):
            """2018~2019년 평균의 두 배를 처음 넘은 해."""
            base = [v for v, y in zip(vals, years) if y in (2018, 2019)]
            b = sum(base) / len(base) if base else 0
            return next((y for v, y in zip(vals, years) if y >= 2020 and b > 0 and v >= 2 * b), None), b

        other = []
        for name, s in raw["series"].items():
            if name in link["queries"]:
                ys = sorted(int(y) for y in s)
                vals = [s[str(y)] for y in ys]
                dy, b = doubled(vals, ys)
                other.append({"name": name, "years": ys, "values": vals, "base": b, "doubled": dy})
        here = []
        for tname in link["topics"]:
            tp = d["topics"].get(tname)
            if tp:
                vals = [round(x * 100, 2) for x in tp["share"]]
                dy, b = doubled(vals, d["years"])
                here.append({"topic": tname, "ko": labels.get(tname, {}).get("ko") or tname, "years": d["years"], "values": vals,
                             "base": round(b, 2), "doubled": dy, "n": tp["n"]})
        return {"label": link["label"], "field": link["field"], "why": link["why"], "milestones": link.get("milestones", []),
                "other": other, "here": here, "venue": d["meta"]["name"], "collected_at": raw["collected_at"]}, 200
    if path == "/api/draft":
        return draft.write(q.get("q", "").strip().lower(), int(q.get("c", 2026)), d, events(), upstream), 200
    if path == "/api/spread":
        return spread(q.get("q", "").strip().lower(), int(q.get("c", 2026)), derived), 200
    if path == "/api/onto":
        return ontology(q.get("q", "").strip().lower(), int(q.get("c", 2026)), d, derived), 200
    if path == "/api/gapcheck":
        return gaps.gap_check(q.get("q", "").strip().lower(), int(q.get("c", 2023)), d), 200
    if path == "/api/has_llm":
        return {"llm": bool(agent.api_key()), "model": agent.MODEL}, 200
    return {"error": "없는 주소"}, 404
