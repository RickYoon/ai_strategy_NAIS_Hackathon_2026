"""화면이 부르는 주소들. 서버가 요청마다 이 파일을 다시 읽으므로, 고쳐도 서버를 다시 띄울 필요가 없다."""
import importlib
import json
from pathlib import Path

import agent
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


def handle(path, q, derived):
    importlib.reload(agent)
    importlib.reload(gaps)
    d = derived(q.get("venue", "ectc"))
    if path == "/api/meta":
        top = sorted(((t, v["n"]) for t, v in d["topics"].items()), key=lambda x: -x[1])
        return {k: d[k] for k in ("meta", "years", "total", "n_papers", "n_inst", "n_auth", "n_topics", "rules", "backtest")} | {
            "topic_names": [t for t, _ in top]}, 200
    if path == "/api/topic":
        name = q.get("q", "").strip().lower()
        if name not in d["topics"]:
            return {"error": f"'{name}' 주제가 없다 (발표 {d['rules']['min_docs']}편 미만이거나 제목에 없는 말)"}, 404
        return {"topic": name, **d["topics"][name], "events": events().get(name, []), "upstream": upstream(name)}, 200
    if path == "/api/who":
        kind, name = q.get("kind", "inst"), q.get("name", "")
        w = d.get("who", {}).get(kind, {}).get(name)
        return ({"kind": kind, "name": name, **w}, 200) if w else ({"error": "발표가 적어 흐름을 만들지 않았다"}, 404)
    if path == "/api/agent":
        return agent.run(q.get("q", ""), int(q.get("c", 2023)), d, events(), upstream), 200
    if path == "/api/gap":
        return gaps.gap_map(q.get("q", "").strip().lower(), int(q.get("c", 2023)), d), 200
    if path == "/api/find":
        c = int(q.get("c", 2026))
        cand = agent.candidates(d, c)
        labels = agent.label_topics([x["topic"] for x in cand])
        shown = [{**x, "ko": labels.get(x["topic"], {}).get("ko")} for x in cand if labels.get(x["topic"], {"keep": True}).get("keep", True)]
        bt = next((b for b in d["backtest"] if b["cutoff"] == c), None)
        grew = {x["topic"]: x["grew"] for x in bt["lit_topics"]} if bt else {}
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
        return {"past": {"cutoff": last["cutoff"], "opened": last["opened"], "items": past}, "cutoff": c, "candidates": shown, "removed": len(cand) - len(shown), "labeled": bool(labels), "traits": agent.traits(d), "n_topics": d["n_topics"], "n_eligible": len(cand)}, 200
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
    if path == "/api/has_llm":
        return {"llm": bool(agent.api_key()), "model": agent.MODEL}, 200
    return {"error": "없는 주소"}, 404
