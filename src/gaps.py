"""틈 지도: 주제의 발표를 '다루는 문제 × 쓰는 방법'으로 나눠, 어디가 비었는지 본다.

- 분류는 LLM이 제목만 보고 한다 (거칠다. 사람이 확인해야 한다).
- 개수는 코드가 센다. 칸마다 원문 목록이 붙는다.
- 기준 연도 뒤의 발표는 LLM에게 주지 않는다.
- 같은 주제 · 같은 기준 연도는 한 번 분류하면 data/derived/gaps/ 에 남겨 다시 쓴다.
"""
import json
import re
from pathlib import Path

import agent

ROOT = Path(__file__).resolve().parent.parent
MAX_TITLES = 180

PROMPT = """아래는 한 연구 주제("{topic}")의 학회 발표 제목이다. 번호가 붙어 있다.
1) 이 제목들이 다루는 '문제'를 5~6개로, '방법(접근)'을 5~6개로 묶어 한국어 짧은 이름을 붙인다. 제목에 실제로 나오는 것만 쓴다.
2) 제목마다 문제 하나와 방법 하나를 고른다. 제목만으로 알 수 없으면 "불명"으로 둔다.
네가 알고 있는 이후의 일은 쓰지 않는다. 아래 JSON만 출력한다.
{{"problems": ["..."], "methods": ["..."], "items": [[번호, "문제", "방법"]]}}

{titles}"""


def gap_map(topic, cutoff, data):
    t = data["topics"].get(topic)
    if not t:
        return {"error": "없는 주제"}
    cache = ROOT / "data" / "derived" / "gaps" / f"{re.sub(r'[^a-z0-9]+', '_', topic)}_{cutoff}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    key = agent.api_key()
    if not key:
        return {"error": "LLM 키가 없어 분류할 수 없다"}
    papers = [p for p in t["papers"] if p["y"] <= cutoff][-MAX_TITLES:]
    if len(papers) < 10:
        return {"error": "발표가 10편 미만이라 틈 지도를 그리지 않는다"}
    import anthropic
    titles = "\n".join(f"{i}. ({p['y']}) {p['t']}" for i, p in enumerate(papers))
    r = anthropic.Anthropic(api_key=key).messages.create(
        model=agent.MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT.format(topic=topic, titles=titles)}])
    text = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"error": "LLM 응답을 읽지 못했다"}
    probs = [x for x in out.get("problems", []) if x != "불명"]
    meths = [x for x in out.get("methods", []) if x != "불명"]
    cells, unknown, seen = {}, 0, set()
    for it in out.get("items", []):
        if not (isinstance(it, list) and len(it) == 3 and isinstance(it[0], int) and 0 <= it[0] < len(papers)) or it[0] in seen:
            continue
        seen.add(it[0])
        i, pr, me = it
        if pr not in probs or me not in meths:
            unknown += 1
            continue
        c = cells.setdefault(f"{pr}|{me}", {"n": 0, "recent": 0, "papers": []})
        c["n"] += 1
        c["recent"] += papers[i]["y"] >= cutoff - 2
        c["papers"].append({"y": papers[i]["y"], "t": papers[i]["t"], "doi": papers[i]["doi"]})
    res = {"topic": topic, "cutoff": cutoff, "model": agent.MODEL, "problems": probs, "methods": meths, "cells": cells,
           "classified": len(seen) - unknown, "unknown": unknown + (len(papers) - len(seen)), "total": len(papers)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
