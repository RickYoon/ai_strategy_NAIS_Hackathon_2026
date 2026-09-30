"""기획서의 '연구개발 동향' 절 초안을 쓴다 — 과거 발표 기록을 분석해 새 문서를 쓰는 단계.

- 재료는 전부 도구(코드)가 만든다: 흐름, 내용 신호, 산업 사건, 기관, 과거 성적, 근거 논문 목록.
- LLM은 그 재료로 문장만 쓴다. 문장마다 근거(도구 이름 또는 논문 번호)를 단다.
- 검사기가 문장 속 숫자와 논문 번호를 재료와 대조하고, 맞지 않는 문장은 버린다.
- 기준 연도 뒤의 기록은 재료에 넣지 않는다.
"""
import json
import re

import agent

BUDGET = lambda: True  # 서버가 비용 상한 검사 함수를 넣어 준다

PROMPT = """너는 연구 기획서의 '연구개발 동향' 절 초안을 쓰는 조수다. 아래 재료만 써서 한국어로 쓴다.
규칙:
- 지금은 {cutoff}년이다. 그 뒤의 일은 쓰지 않는다. 재료에 없는 사실과 숫자는 쓰지 않는다.
- 숫자는 재료에 있는 그대로 쓴다. 새로 계산하지 않는다.
- "유망하다", "성장할 것이다" 같은 단정은 쓰지 않는다. 판단은 연구자가 한다.
- 문장마다 근거를 단다. refs에는 재료의 도구 이름(read_topic, industry_events, find_people, verify_signal) 또는 논문 번호(정수)를 넣는다.
- 세 문단을 쓴다: "연구 흐름", "내용의 변화와 산업의 움직임", "주요 수행 기관과 유의점". 문단마다 2~3문장.
- 논문을 예로 들 때는 제목을 옮기지 말고 무엇을 다뤘는지 짧게 말하고 번호를 단다.
아래 JSON만 출력한다.
{{"paragraphs": [{{"title": "문단 제목", "sentences": [{{"text": "문장", "refs": ["read_topic", 3]}}]}}]}}

[재료]
{material}"""


def write(topic, cutoff, data, events, upstream):
    key = agent.api_key()
    if not key:
        return {"error": "LLM 키가 없어 초안을 쓸 수 없다"}
    t = data["topics"].get(topic)
    if not t:
        return {"error": "없는 주제"}
    box = agent.Toolbox(data, events, upstream, cutoff)
    mat = {"read_topic": box.read_topic(topic), "industry_events": box.industry_events(topic),
           "find_people": box.find_people(topic), "verify_signal": box.verify_signal()}
    # 근거 논문: 최근 3년의 양산 문제 논문을 먼저, 그다음 최근 논문, 그다음 초기 논문
    ps = [p for p in t["papers"] if p["y"] <= cutoff]
    pick = ([p for p in ps if p["p"] and p["y"] >= cutoff - 2][:10] + [p for p in ps if not p["p"] and p["y"] >= cutoff - 2][:6]
            + [p for p in ps if p["y"] < cutoff - 2][:6])
    papers = [{"no": i + 1, "year": p["y"], "title": p["t"], "institutions": p["inst"][:2], "doi": p["doi"]} for i, p in enumerate(pick)]
    mat["papers"] = [{k: v for k, v in p.items() if k != "doi"} for p in papers]
    if not BUDGET():
        return {"error": "공개 시연의 LLM 호출 상한을 다 썼다"}
    import anthropic
    r = anthropic.Anthropic(api_key=key).messages.create(
        model=agent.MODEL, max_tokens=6000,
        messages=[{"role": "user", "content": PROMPT.format(cutoff=cutoff, material=json.dumps(mat, ensure_ascii=False))}])
    text = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"error": "LLM 응답을 읽지 못했다", "stop": r.stop_reason, "head": text[:200], "tail": text[-200:]}
    tools = {"read_topic", "industry_events", "find_people", "verify_signal"}
    nos = {p["no"] for p in papers}
    paras, dropped = [], []
    for para in out.get("paragraphs", []):
        sents = [s for s in para.get("sentences", []) if isinstance(s, dict) and s.get("text")]
        for s in sents:  # 논문 번호 표기([3])는 숫자 검사에서 빼고 본다
            s["_plain"] = re.sub(r"\[\d+\]", "", s["text"])
        ok, bad = agent.check([{"text": s["_plain"], "i": i} for i, s in enumerate(sents)], [mat])
        bad_i = {b["i"]: b["missing"] for b in bad}
        keep = []
        for i, s in enumerate(sents):
            refs = [x for x in s.get("refs", []) if x in tools or x in nos]
            if i in bad_i:
                dropped.append({"text": s["text"], "why": "재료에 없는 숫자 " + ", ".join(bad_i[i])})
            elif not refs:
                dropped.append({"text": s["text"], "why": "근거 표시 없음"})
            else:
                keep.append({"text": s["text"], "refs": refs})
        if keep:
            paras.append({"title": para.get("title", ""), "sentences": keep})
    used = sorted({x for p in paras for s in p["sentences"] for x in s["refs"] if isinstance(x, int)})
    return {"topic": topic, "cutoff": cutoff, "model": agent.MODEL, "paragraphs": paras, "dropped": dropped,
            "papers": [p for p in papers if p["no"] in used], "papers_given": len(papers)}
