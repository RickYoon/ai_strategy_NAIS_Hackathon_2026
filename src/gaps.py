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
2) 제목마다 문제 하나와 방법 하나를 고른다. 이름 대신 목록에서의 순번(1부터)으로 적는다. 제목만으로 알 수 없으면 0.
네가 알고 있는 이후의 일은 쓰지 않는다. 아래 JSON만 출력한다. 설명을 덧붙이지 않는다.
{{"problems": ["..."], "methods": ["..."], "items": [[제목 번호, 문제 순번, 방법 순번]]}}

{titles}"""

PROMPT_FIXED = """아래는 한 연구 주제("{topic}")의 학회 발표 제목이다. 번호가 붙어 있다.
문제 목록과 방법 목록은 이미 정해져 있다. 새로 만들지 않는다.
문제: {problems}
방법: {methods}
제목마다 문제 하나와 방법 하나를 골라 목록에서의 순번(1부터)으로 적는다. 어느 것에도 맞지 않거나 제목만으로 알 수 없으면 0.
아래 JSON만 출력한다. 설명을 덧붙이지 않는다.
{{"items": [[제목 번호, 문제 순번, 방법 순번]]}}

{titles}"""


def VP(data):
    """저장 파일 이름 앞에 붙이는 학회 표시. ECTC는 예전 이름을 그대로 쓴다."""
    v = data["meta"].get("venue", "ectc")
    return "" if v == "ectc" else v + "__"


def ask(prompt):
    import anthropic
    r = anthropic.Anthropic(api_key=agent.api_key()).messages.create(
        model=agent.MODEL, max_tokens=8000, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return None


def pairs(out, probs, meths, n):
    """LLM이 적은 [번호, 문제 순번, 방법 순번]을 검사해서 (번호, 문제, 방법)으로 바꾼다. 어긋난 줄은 버린다."""
    seen, ok = set(), []
    for it in out.get("items", []):
        if not (isinstance(it, list) and len(it) == 3 and all(isinstance(x, int) for x in it)) or not 0 <= it[0] < n or it[0] in seen:
            continue
        seen.add(it[0])
        if 1 <= it[1] <= len(probs) and 1 <= it[2] <= len(meths):
            ok.append((it[0], probs[it[1] - 1], meths[it[2] - 1]))
    return ok, seen


def gap_map(topic, cutoff, data):
    t = data["topics"].get(topic)
    if not t:
        return {"error": "없는 주제"}
    cache = ROOT / "data" / "derived" / "gaps" / f"{VP(data)}{re.sub(r'[^a-z0-9]+', '_', topic)}_{cutoff}_v2.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    key = agent.api_key()
    if not key:
        return {"error": "LLM 키가 없어 분류할 수 없다"}
    papers = [p for p in t["papers"] if p["y"] <= cutoff][-MAX_TITLES:]
    if len(papers) < 10:
        return {"error": "발표가 10편 미만이라 틈 지도를 그리지 않는다"}
    titles = "\n".join(f"{i}. ({p['y']}) {p['t']}" for i, p in enumerate(papers))
    out = ask(PROMPT.format(topic=topic, titles=titles))
    if not out:
        return {"error": "LLM 응답을 읽지 못했다"}
    probs, meths = out.get("problems", []), out.get("methods", [])
    ok, seen = pairs(out, probs, meths, len(papers))
    cells = {}
    for i, pr, me in ok:
        c = cells.setdefault(f"{pr}|{me}", {"n": 0, "recent": 0, "papers": []})
        c["n"] += 1
        c["recent"] += papers[i]["y"] >= cutoff - 2
        c["papers"].append({"y": papers[i]["y"], "t": papers[i]["t"], "doi": papers[i]["doi"], "inst": papers[i]["inst"][:3]})
    unknown = len(seen) - len(ok)
    # ── 칸이 채워진 순서: 언제, 누가 먼저
    for c in cells.values():
        c["papers"].sort(key=lambda p: p["y"])
        c["first_year"], c["last_year"] = c["papers"][0]["y"], c["papers"][-1]["y"]
        first = [p for p in c["papers"] if p["y"] == c["first_year"]]
        c["first_by"] = sorted({n for p in first for n in p["inst"]})[:3]
    order = sorted(({"problem": k.split("|")[0], "method": k.split("|")[1], "first_year": c["first_year"], "first_by": c["first_by"],
                     "n": c["n"], "recent": c["recent"]} for k, c in cells.items()), key=lambda x: (x["first_year"], -x["n"]))
    # ── 빈 칸 가운데, 같은 문제 줄과 같은 방법 줄이 최근 3년에 채워지고 있는 칸 (힌트일 뿐 추천이 아니다)
    row = {p: sum(c["recent"] for k, c in cells.items() if k.split("|")[0] == p) for p in probs}
    col = {m: sum(c["recent"] for k, c in cells.items() if k.split("|")[1] == m) for m in meths}
    hints = sorted(({"problem": p, "method": m, "problem_recent": row[p], "method_recent": col[m]}
                    for p in probs for m in meths if f"{p}|{m}" not in cells and row[p] and col[m]),
                   key=lambda x: -min(x["problem_recent"], x["method_recent"]))[:4]
    res = {"topic": topic, "cutoff": cutoff, "model": agent.MODEL, "problems": probs, "methods": meths, "cells": cells,
           "order": order, "hints": hints, "recent_from": cutoff - 2,
           "classified": len(seen) - unknown, "unknown": unknown + (len(papers) - len(seen)), "total": len(papers)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def gap_check(topic, cutoff, data):
    """과거로 돌아가 그린 지도의 빈 칸이 그 뒤 3년 안에 채워졌는지 본다.

    지도(문제 · 방법 목록과 빈 칸 힌트)는 기준 연도까지의 제목만으로 만든다.
    그 뒤 3년의 제목은 같은 목록에 끼워 넣기만 한다(목록을 새로 만들지 않는다).
    """
    t = data["topics"].get(topic)
    if not t:
        return {"error": "없는 주제"}
    if cutoff + 1 > data["years"][-1]:
        return {"error": "기준 연도 뒤의 기록이 없어 확인할 수 없다"}
    base = gap_map(topic, cutoff, data)
    if "error" in base:
        return base
    cache = ROOT / "data" / "derived" / "gaps" / f"{VP(data)}{re.sub(r'[^a-z0-9]+', '_', topic)}_{cutoff}_check.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    later = [p for p in t["papers"] if cutoff < p["y"] <= cutoff + 3][:MAX_TITLES]
    if not later:
        return {"error": "그 뒤 3년의 발표가 없다"}
    probs, meths = base["problems"], base["methods"]
    out = ask(PROMPT_FIXED.format(topic=topic, problems=" / ".join(f"{i+1}. {x}" for i, x in enumerate(probs)),
                                  methods=" / ".join(f"{i+1}. {x}" for i, x in enumerate(meths)),
                                  titles="\n".join(f"{i}. ({p['y']}) {p['t']}" for i, p in enumerate(later))))
    if not out:
        return {"error": "LLM 응답을 읽지 못했다"}
    ok, seen = pairs(out, probs, meths, len(later))
    filled = {}
    for i, pr, me in ok:
        filled.setdefault(f"{pr}|{me}", []).append({"y": later[i]["y"], "t": later[i]["t"], "doi": later[i]["doi"], "inst": later[i]["inst"][:3]})
    empty = [f"{p}|{m}" for p in probs for m in meths if f"{p}|{m}" not in base["cells"]]
    hinted = [f"{h['problem']}|{h['method']}" for h in base["hints"]]
    rest = [k for k in empty if k not in hinted]
    row = lambda k: {"problem": k.split("|")[0], "method": k.split("|")[1], "filled": k in filled,
                     "papers": sorted(filled.get(k, []), key=lambda p: p["y"])[:4], "n": len(filled.get(k, []))}
    res = {"topic": topic, "cutoff": cutoff, "opened": f"{cutoff+1}~{min(cutoff+3, data['years'][-1])}", "model": agent.MODEL,
           "later_papers": len(later), "later_classified": len(ok),
           "hinted": [row(k) for k in hinted], "hinted_filled": sum(k in filled for k in hinted),
           "other_empty": len(rest), "other_filled": sum(k in filled for k in rest),
           "other": [row(k) for k in rest]}
    cache.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
