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


def model_scores(venue, d, topics, c, vy):
    """src/learn.py 와 같은 특징 · 같은 모델로, 과거 세 시점 전부를 배워 지금 후보에 점수를 매긴다."""
    import learn
    import research
    cache = combo.__dict__.setdefault("_model", {})
    if venue not in cache:
        labels = json.loads(agent.LABELS.read_text(encoding="utf-8")) if agent.LABELS.exists() else {}
        X, y, _, _ = learn.dataset(venue, labels, vy)
        predict, _ = learn.fit_logistic(X, y)
        cache[venue] = (predict, research.raw_index(venue))
    predict, (rows, idx, total) = cache[venue]
    import numpy as np
    out = {}
    for tp in topics:
        f = learn.feats(rows, idx, total, tp, c, vy, d["years"][0])
        if f is not None:
            out[tp] = float(predict(np.array([f], float))[0])
    return out


def learned_summary():
    """src/learn.py 가 저장한 배운 모델의 시험 결과 (배우지 않은 해 · 학회)."""
    f = ROOT / "data" / "derived" / "learn.json"
    if not f.exists():
        return None
    r = json.loads(f.read_text(encoding="utf-8"))
    pick = lambda i: {"top": r[i]["top20"], "base": r[i]["base"]}
    return {"time": pick(0), "venue": pick(1)}


def topic_rank(venue, d, topic, c):
    """지금 시점에서 이 주제의 배운 점수 순위 (후보 가운데, 전체 활발한 주제 가운데)."""
    if c != d["years"][-1]:
        return None
    vy = combo.__dict__.get("_vy") or combo.__dict__.setdefault("_vy", combo.venue_years())
    cand = agent.candidates(d, c)
    labels = json.loads(agent.LABELS.read_text(encoding="utf-8")) if agent.LABELS.exists() else {}
    act = [x["topic"] for x in cand if x["active"] and labels.get(x["topic"], {"keep": True}).get("keep", True)]
    names = list(dict.fromkeys(act + [topic]))
    ms = model_scores(venue, d, names, c, vy)
    if topic not in ms:
        return None
    ranked = sorted([k for k in act if k in ms], key=lambda k: -ms[k])
    return {"score": round(ms[topic], 3), "is_candidate": topic in act,
            "rank": ranked.index(topic) + 1 if topic in ranked else None, "of": len(ranked),
            "beats": sum(1 for k in ranked if ms[k] < ms[topic]),
            "parts": score_parts(venue, d, topic, c, vy), "past": past_picks(venue, labels)}


def score_parts(venue, d, topic, c, vy):
    """배운 점수를 지표별로 쪼갠다: 가중치 × (이 주제 값 − 배운 자료의 평균) / 표준편차. 다 더하면 점수가 된다."""
    import learn
    import numpy as np
    predict, (rows, idx, total) = combo.__dict__["_model"][venue]
    f = learn.feats(rows, idx, total, topic, c, vy, d["years"][0])
    if f is None:
        return None
    z = (np.array(f, float) - predict.mu) / predict.sd
    return {"base": round(float(predict.b), 3),
            "items": [{"name": n, "value": round(float(v), 4), "avg": round(float(m), 4), "w": round(float(w), 3), "part": round(float(w * zz), 3)}
                      for n, v, m, w, zz in zip(learn.FEATS, f, predict.mu, predict.w, z)]}


def past_picks(venue, labels):
    """배우지 않은 해로 시험: 2021 · 2022년으로 배운 모델이 2023년에 위 20%로 꼽았을 주제와 3년 뒤 결과."""
    cache = combo.__dict__.setdefault("_past", {})
    if venue not in cache:
        import learn
        import numpy as np
        vy = combo.__dict__.get("_vy") or combo.__dict__.setdefault("_vy", combo.venue_years())
        X, y, cs, names = learn.dataset(venue, labels, vy)
        predict, _ = learn.fit_logistic(X[cs < 2023], y[cs < 2023])
        m = cs == 2023
        p, yy, nn = predict(X[m]), y[m], [n for n, k in zip(names, m) if k]
        k = max(1, round(len(yy) * 0.2))
        top = np.argsort(-p)[:k]
        ko = lambda t: labels.get(t, {}).get("ko") or t
        cache[venue] = {"cutoff": 2023, "of": int(len(yy)), "base_grew": int(yy.sum()),
                        "picks": [{"topic": nn[i], "ko": ko(nn[i]), "grew": bool(yy[i])} for i in top]}
    return cache[venue]


def topic_series(venue, d, topic):
    """주제 화면 그림에서 고를 수 있는 연도별 지표. 배운 점수의 특징과 같은 재료(제목 · 소속)로 센다."""
    import research
    import analyze
    cache = combo.__dict__.setdefault("_raw", {})
    if venue not in cache:
        cache[venue] = research.raw_index(venue)
    rows, idx, _ = cache[venue]
    ps = [rows[k] for k in idx.get(topic, [])]
    seen, out = set(), {k: [] for k in ("papers", "problem", "inst", "newinst", "company", "coop")}
    for y in d["years"]:
        g = [p for p in ps if p[0] == y]
        ins = {n for p in g for n, _, _ in p[2]}
        fr = (lambda f: round(sum(1 for p in g if f(p)) / len(g), 4)) if g else (lambda f: None)
        out["papers"].append(len(g))
        out["problem"].append(fr(lambda p: bool(analyze.PROBLEM.search(" ".join(p[1])))))
        out["inst"].append(len(ins))
        out["newinst"].append(len(ins - seen))
        out["company"].append(fr(lambda p: any(tp == "company" for _, tp, _ in p[2])))
        out["coop"].append(fr(lambda p: len({n for n, _, _ in p[2]}) >= 2))
        seen |= ins
    return out


def _learned_rank(venue, d, topics, c):
    """에이전트의 후보 도구에 붙일 배운 점수 순위 (후보 가운데)."""
    try:
        vy = combo.__dict__.get("_vy") or combo.__dict__.setdefault("_vy", combo.venue_years())
        ms = model_scores(venue, d, topics, c, vy)
        ranked = sorted([t for t in topics if t in ms], key=lambda t: -ms[t])
        return {t: i + 1 for i, t in enumerate(ranked)}
    except Exception:
        return {}


def topic_groups(venue, act, c, cut=0.4, least=5):
    """후보끼리 묶는다: 최근 3년 발표 가운데 두 주제가 같은 제목에 함께 나온 비율(작은 쪽 기준)이 40% 이상이고 5편 이상이면 같은 묶음.
    사람이나 LLM이 묶지 않는다 — 제목이 겹치는 것만 본다."""
    import research
    cache = combo.__dict__.setdefault("_raw", {})
    if venue not in cache:
        cache[venue] = research.raw_index(venue)
    rows, idx, _ = cache[venue]
    S = {x["topic"]: {k for k in idx.get(x["topic"], []) if c - 2 <= rows[k][0] <= c} for x in act}
    par = {t: t for t in S}
    find = lambda t: t if par[t] == t else find(par[t])
    names = list(S)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            n = len(S[a] & S[b])
            if n >= least and n / max(1, min(len(S[a]), len(S[b]))) >= cut:
                par[find(a)] = find(b)
    g = {}
    for t in names:
        g.setdefault(find(t), []).append(t)
    info = {x["topic"]: x for x in act}
    out = []
    for m in g.values():
        if len(m) < 2:
            continue
        m.sort(key=lambda t: -len(S[t]))
        papers = set().union(*(S[t] for t in m))
        out.append({"topics": [{"topic": t, "ko": info[t].get("ko") or t, "rank": info[t].get("model_rank")} for t in m],
                    "papers": len(papers), "events": sum(info[t].get("events", 0) for t in m)})
    return sorted(out, key=lambda x: -x["papers"])


def combo_result(venue):
    """지표를 겹친 개수별 과거 성적 (src/combo.py 가 계산해 둔 것)."""
    f = ROOT / "data" / "derived" / "combo.json"
    if not f.exists():
        return None
    return next((r for r in json.loads(f.read_text(encoding="utf-8")) if r["venue"] == venue), None)


import os
LLM_CAP = int(os.environ.get("NAIS_LLM_CAP", "60"))  # 서버를 한 번 띄운 동안 LLM을 부를 수 있는 횟수 (공개 시연 비용 상한)
LLM_USED = {"n": 0}


def llm_budget():
    """상한을 넘으면 False. 넘은 뒤에는 저장된 응답만 보여준다."""
    if LLM_USED["n"] >= LLM_CAP:
        return False
    LLM_USED["n"] += 1
    return True


def handle(path, q, derived):
    importlib.reload(agent)
    importlib.reload(gaps)
    importlib.reload(draft)
    gaps.BUDGET = draft.BUDGET = llm_budget
    d = derived(q.get("venue", "ectc"))
    if path == "/api/meta":
        top = sorted(((t, v["n"]) for t, v in d["topics"].items()), key=lambda x: -x[1])
        return {k: d[k] for k in ("meta", "years", "total", "n_papers", "n_inst", "n_auth", "n_topics", "rules", "backtest")} | {
            "topic_names": [t for t, _ in top], "score": agent.scorecard(d), "learned": learned_summary()}, 200
    if path == "/api/topic":
        name = q.get("q", "").strip().lower()
        if name not in d["topics"]:
            return {"error": f"'{name}' 주제가 없다 (발표 {d['rules']['min_docs']}편 미만이거나 제목에 없는 말)"}, 404
        lab = json.loads(agent.LABELS.read_text(encoding="utf-8")).get(name, {}) if agent.LABELS.exists() else {}
        rk = None
        try:
            rk = topic_rank(q.get("venue", "ectc"), d, name, d["years"][-1])
        except Exception:
            pass
        try:
            ser = topic_series(q.get("venue", "ectc"), d, name)
        except Exception:
            ser = None
        return {"topic": name, "ko": lab.get("ko"), "desc": lab.get("desc"), **d["topics"][name], "events": events().get(name, []),
                "upstream": upstream(name), "rank": rk, "series": ser}, 200
    if path == "/api/actors":
        venue, kind = q.get("venue", "ectc"), q.get("kind", "inst")
        cache = combo.__dict__.setdefault("_actors", {})
        if (venue, kind) not in cache:
            import actors
            c = d["years"][-1]
            labels = json.loads(agent.LABELS.read_text(encoding="utf-8")) if agent.LABELS.exists() else {}
            keep = lambda t: labels.get(t, {"keep": True}).get("keep", True)
            cand = [x["topic"] for x in agent.candidates(d, c) if x["active"] and keep(x["topic"])]
            bt = next(b for b in d["backtest"] if b["cutoff"] == 2023)
            hot = {x["topic"]: bool(x["grew"]) for x in bt["lit_topics"] if x["hot"] and keep(x["topic"])}
            hot.update({x["topic"]: bool(x["grew"]) for x in bt.get("unlit_hot", []) if keep(x["topic"])})
            ko = {t: v.get("ko") for t, v in labels.items()}
            cache[(venue, kind)] = actors.build(venue, cand, ko, hot, c, kind)
        return cache[(venue, kind)], 200
    if path == "/api/graph":
        # 데이터 출처 + 지금 만들어진 온톨로지 그래프: 학회 — 후보 주제 — 기관 (최근 3년 발표 기록에서 센다)
        cache = combo.__dict__.setdefault("_graph", {})
        if "g" not in cache:
            import analyze as _an
            from collections import Counter, defaultdict
            c = d["years"][-1]
            labels = json.loads(agent.LABELS.read_text(encoding="utf-8")) if agent.LABELS.exists() else {}
            keep = lambda t: labels.get(t, {"keep": True}).get("keep", True)
            cand = [x["topic"] for x in agent.candidates(d, c) if x["active"] and keep(x["topic"])]
            cs = set(cand)
            sources, tv, ti, ta, ai, tot_i, tot_a = [], Counter(), defaultdict(Counter), defaultdict(Counter), defaultdict(Counter), set(), set()
            for m in _an.GROUPS["pkg"]["members"] + ["pvsc"]:
                mf = ROOT / "data" / "raw" / f"{m}.meta.json"
                if not mf.exists():
                    continue
                meta = json.loads(mf.read_text(encoding="utf-8"))
                n_i, n_a = set(), set()
                for line in open(ROOT / "data" / "raw" / f"{m}.jsonl", encoding="utf-8"):
                    r = json.loads(line)
                    if not r["year"]:
                        continue
                    ins = {i["name"] for a_ in r["authors"] for i in a_["inst"] if i["name"]}
                    n_i |= ins; n_a |= {a_["name"] for a_ in r["authors"] if a_.get("name")}
                    if m == "pvsc" or r["year"] < c - 2:
                        continue
                    ts = cs & set(_an.terms(r["title"]))
                    for t in ts:
                        tv[(t, m)] += 1
                        for i in ins:
                            ti[t][i] += 1
                        for a_ in r["authors"]:
                            if a_.get("name"):
                                ta[t][a_["name"]] += 1
                                for i in a_["inst"]:
                                    if i["name"]:
                                        ai[a_["name"]][i["name"]] += 1
                sources.append({"venue": m, "name": meta["name"], "field": meta.get("field"), "years": meta["years"], "count": meta["count"],
                                "collected_at": meta["collected_at"][:10], "inst": len(n_i), "auth": len(n_a), "in_graph": m != "pvsc"})
                if m != "pvsc":
                    tot_i |= n_i; tot_a |= n_a
            ev = events()
            _evraw = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
            ko = lambda t: labels.get(t, {}).get("ko") or t
            nodes = [{"id": "v:" + m, "kind": "venue", "label": next(s_["name"] for s_ in sources if s_["venue"] == m).split(" ")[0]} for m in _an.GROUPS["pkg"]["members"]]
            nodes += [{"id": "t:" + t, "kind": "topic", "label": ko(t), "events": len(ev.get(t, []))} for t in cand]
            insts = {}
            for t in cand:
                for i, n in ti[t].most_common(3):
                    insts.setdefault(i, 0); insts[i] += n
            auths = {}
            for t in cand:
                for a_, n in ta[t].most_common(2):
                    auths.setdefault(a_, 0); auths[a_] += n
            for a_ in auths:  # 저자의 소속(가장 많이 적힌 곳)이 그래프에 없으면 넣는다
                if ai[a_]:
                    top_i = ai[a_].most_common(1)[0][0]
                    insts.setdefault(top_i, 0)
            nodes += [{"id": "i:" + i, "kind": "inst", "label": i} for i in insts]
            nodes += [{"id": "a:" + a_, "kind": "auth", "label": a_} for a_ in auths]
            edges = [{"a": "t:" + t, "b": "v:" + m, "w": n} for (t, m), n in tv.items()]
            edges += [{"a": "t:" + t, "b": "i:" + i, "w": n} for t in cand for i, n in ti[t].most_common(3)]
            edges += [{"a": "t:" + t, "b": "a:" + a_, "w": n} for t in cand for a_, n in ta[t].most_common(2)]
            edges += [{"a": "a:" + a_, "b": "i:" + ai[a_].most_common(1)[0][0], "w": ai[a_].most_common(1)[0][1]} for a_ in auths if ai[a_]]
            cache["g"] = {"sources": sources, "nodes": nodes, "edges": edges, "events": sum(len(v) for k, v in _evraw.items() if not k.startswith("_")), "event_topics": sum(1 for k in _evraw if not k.startswith("_")),
                          "schema": {"papers": sum(s_["count"] for s_ in sources if s_["in_graph"]), "inst": len(tot_i), "auth": len(tot_a),
                                     "topics": d["n_topics"], "candidates": len(cand), "features": 16}}
        return cache["g"], 200
    if path == "/api/who":
        kind, name = q.get("kind", "inst"), q.get("name", "")
        w = d.get("who", {}).get(kind, {}).get(name)
        return ({"kind": kind, "name": name, **w}, 200) if w else ({"error": "발표가 적어 흐름을 만들지 않았다"}, 404)
    if path == "/api/agent":
        c = int(q.get("c", 2026))
        import hashlib
        qs = q.get("q", "").strip()
        key = hashlib.sha1(f"{q.get('venue', 'ectc')}|{c}|{qs}".encode()).hexdigest()[:16]
        cf = ROOT / "data" / "derived" / "agent_cache" / f"{key}.json"
        try:
            if not cf.exists() or q.get("fresh"):
                if not llm_budget():
                    raise RuntimeError(f"공개 시연 상한({LLM_CAP}회)을 다 썼다")
            else:
                raise RuntimeError("같은 질문의 저장된 응답을 쓴다 (비용 절약)")
            res = agent.run(qs, c, d, events(), upstream,
                            {"spread": lambda tp: spread(tp, c, derived), "relations": lambda tp: ontology(tp, c, d, derived),
                             "rank": (lambda tps: _learned_rank(q.get("venue", "ectc"), d, tps, c)) if c == d["years"][-1] else None})
        except Exception as e:  # 네트워크 · 키 문제
            res = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        if "error" not in res:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps({**res, "question": qs}, ensure_ascii=False), encoding="utf-8")
        elif cf.exists():
            # LLM을 부를 수 없을 때: 같은 질문에 대해 전에 실제로 받은 응답을 그대로 보여준다 (화면에 표시)
            res = {**json.loads(cf.read_text(encoding="utf-8")), "cached": True, "cache_reason": res["error"][:120]}
        import re
        for x in res.get("sentences") or []:  # 영어 필드 이름이 문장에 새어 나오면 지운다 (저장된 응답 포함)
            x["text"] = re.sub(r"\s*\((learned_agent|back_to_2023|unseen_conferences|[a-z]+_[a-z_]+)\)", "", x.get("text", ""))
        return res, 200
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
        # 배운 점수: 과거 세 시점의 결과로 배운 모델의 점수(지금 시점에서만 — 과거로 돌아간 화면에서는 미래가 섞이므로 쓰지 않는다)
        if c == d["years"][-1]:
            try:
                ms = model_scores(q.get("venue", "ectc"), d, [x["topic"] for x in shown], c, vy)
                act = {x["topic"] for x in shown if x["active"]}
                ranked = sorted([k for k in ms if k in act], key=lambda k: -ms[k]) + sorted([k for k in ms if k not in act], key=lambda k: -ms[k])
                for x in shown:
                    if x["topic"] in ms:
                        x["model_score"] = round(ms[x["topic"]], 3)
                        x["model_rank"] = ranked.index(x["topic"]) + 1
            except Exception:
                pass
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
        try:
            groups = topic_groups(q.get("venue", "ectc"), [x for x in shown if x["active"]], c)
        except Exception:
            groups = []
        return {"past": {"cutoff": last["cutoff"], "opened": last["opened"], "items": past}, "cutoff": c, "candidates": shown, "groups": groups, "removed": len(cand) - len(shown), "labeled": bool(labels), "traits": agent.traits(d), "n_topics": d["n_topics"], "n_eligible": len(cand), "combo": combo_result(q.get("venue", "ectc"))}, 200
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
        return {"llm": bool(agent.api_key()), "model": agent.MODEL, "used": LLM_USED["n"], "cap": LLM_CAP}, 200
    return {"error": "없는 주소"}, 404
