"""LLM 에이전트: 질문을 받아 스스로 계획하고, 도구를 골라 부르고, 근거가 달린 문장만 남긴다.

- 숫자는 도구(코드)가 센다. LLM은 도구 결과에 있는 숫자만 쓸 수 있다.
- 모든 도구는 기준 연도 뒤의 기록을 돌려주지 않는다 (기준 연도 가드).
- 마지막에 검사기가 문장마다 숫자를 도구 결과와 대조하고, 없는 숫자가 든 문장은 버린다.

키: 환경 변수 ANTHROPIC_API_KEY 또는 ~/.config/anthropic/key
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("NAIS_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 8


def api_key():
    k = os.environ.get("ANTHROPIC_API_KEY")
    f = Path.home() / ".config" / "anthropic" / "key"
    if not k and f.exists():
        k = f.read_text().strip()
    return k


TOOLS = [
    {"name": "search_topics", "description": "학회 발표 제목에서 뽑은 주제 이름을 찾는다. 영어 낱말 하나를 넣는다. 기준 연도까지의 발표 수와 함께 돌려준다.",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]}},
    {"name": "read_topic", "description": "주제의 연도별 발표 비중과, 양산 문제를 다룬 제목의 비중이 어떻게 바뀌었는지(내용 신호)를 돌려준다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "industry_events", "description": "이 주제에 사람이 적어 둔 산업 사건(착공 · 발표 · 로드맵)을 돌려준다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "find_people", "description": "이 주제를 실제로 발표한 기관과 저자를 발표 수 순으로 돌려준다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "verify_signal", "description": "같은 내용 신호를 과거 시점에 돌렸을 때 몇 개 중 몇 개가 3년 뒤 실제로 커졌는지 돌려준다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cross_field", "description": "이 주제에 사람이 적어 둔 다른 분야 연결이 있으면, 그 분야의 연도별 논문 수를 돌려준다. 검증되지 않은 가설이다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
]

SYSTEM = """너는 연구 기획자를 돕는 에이전트다. 사용자가 연구 주제를 말하면 도구를 써서 그 주제를 판단할 근거를 모은다.
규칙:
1. 지금은 {cutoff}년이라고 가정한다. 그 뒤의 일은 모른다. 네가 원래 알고 있는 지식으로 미래를 말하지 않는다.
2. 숫자는 도구 결과에 있는 것만 그대로 쓴다. 계산해서 새 숫자를 만들지 않는다.
3. 먼저 search_topics로 사용자의 말에 맞는 주제 이름을 찾는다. 한글 주제는 영어 낱말로 바꿔 찾는다. 결과가 없으면 다른 낱말로 다시 찾는다.
4. 그다음 read_topic, industry_events, find_people, verify_signal을 쓴다. cross_field는 있으면 쓴다.
5. "성장한다", "유망하다"고 단정하지 않는다. 판정은 연구자가 한다.
6. 마지막에는 아래 JSON만 출력한다.
{{"topic": "고른 주제 이름", "sentences": [{{"text": "한국어 한 문장", "from": "근거가 된 도구 이름"}}], "caution": "이 근거의 한계 한 문장"}}
문장은 4~6개. 한 문장에 사실 하나."""


class Toolbox:
    """도구 모음. 전부 기준 연도 가드를 거친다."""

    def __init__(self, data, events, upstream, cutoff):
        self.d, self.ev, self.up, self.c = data, events, upstream, cutoff
        self.yi = [i for i, y in enumerate(data["years"]) if y <= cutoff]

    def search_topics(self, keyword):
        k = keyword.strip().lower()
        hit = []
        for name, t in self.d["topics"].items():
            if k and k in name:
                n = sum(t["count"][i] for i in self.yi)
                if n:
                    hit.append({"topic": name, "papers_until_cutoff": n})
        return sorted(hit, key=lambda x: -x["papers_until_cutoff"])[:12]

    def _t(self, topic):
        return self.d["topics"].get(topic.strip().lower())

    def read_topic(self, topic):
        t = self._t(topic)
        if not t:
            return {"error": "없는 주제. search_topics로 먼저 찾는다."}
        j = t["judge"].get(str(self.c))
        return {"topic": topic, "share_percent_by_year": {str(self.d["years"][i]): round(t["share"][i] * 100, 1) for i in self.yi},
                "papers_by_year": {str(self.d["years"][i]): t["count"][i] for i in self.yi},
                "content_signal": None if not j or "p_before" not in j else {
                    "problem_share_before_percent": round(j["p_before"] * 100), "problem_share_recent_percent": round(j["p_recent"] * 100),
                    "before": f"{j['k_before']}/{j['n_before']}편 (2018~{self.c-3})", "recent": f"{j['k_recent']}/{j['n_recent']}편 ({self.c-2}~{self.c})",
                    "signal_on": j["lit"]}}

    def industry_events(self, topic):
        return [e for e in self.ev.get(topic.strip().lower(), []) if int(e["date"][:4]) <= self.c]

    def find_people(self, topic):
        t = self._t(topic)
        if not t:
            return {"error": "없는 주제"}
        inst, auth = {}, {}
        for p in t["papers"]:
            if p["y"] <= self.c:
                for n in p["inst"]:
                    inst[n] = inst.get(n, 0) + 1
                for n in p["auth"]:
                    auth[n] = auth.get(n, 0) + 1
        top = lambda o, k: [{"name": n, "papers": v} for n, v in sorted(o.items(), key=lambda x: -x[1])[:k]]
        return {"institutions": len(inst), "authors": len(auth), "top_institutions": top(inst, 6), "top_authors": top(auth, 5)}

    def verify_signal(self):
        return [{"cutoff": b["cutoff"], "opened": b["opened"], "active_topics_signal_on": b["hot_lit"], "of_which_grew": b["hot_lit_grew"],
                 "active_topics_signal_off": b["hot_unlit"], "of_which_grew_off": b["hot_unlit_grew"]}
                for b in self.d["backtest"] if b["cutoff"] <= self.c]

    def cross_field(self, topic):
        u = self.up(topic.strip().lower())
        if not u:
            return {"note": "이 주제에 적어 둔 다른 분야 연결이 없다"}
        return {"label": u["label"], "hypothesis": u["why"], "verified": False,
                "papers_by_year": {q: {y: n for y, n in s.items() if int(y) <= self.c} for q, s in u["series"].items()}}


def check(sentences, tool_results):
    """문장에 나온 숫자가 도구 결과에 실제로 있는지 본다. 없으면 버린다."""
    blob = json.dumps(tool_results, ensure_ascii=False)
    have = set(re.findall(r"\d+(?:\.\d+)?", blob.replace(",", "")))
    kept, dropped = [], []
    for s in sentences:
        nums = re.findall(r"\d+(?:\.\d+)?", s["text"].replace(",", ""))
        bad = [n for n in nums if n not in have and n.rstrip("0").rstrip(".") not in have]
        (dropped if bad else kept).append({**s, **({"missing": bad} if bad else {})})
    return kept, dropped


def run(question, cutoff, data, events, upstream):
    key = api_key()
    if not key:
        return {"error": "LLM 키가 없다 (ANTHROPIC_API_KEY)"}
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    box = Toolbox(data, events, upstream, cutoff)
    messages = [{"role": "user", "content": question}]
    trace, results = [], []
    for _ in range(MAX_TURNS):
        r = client.messages.create(model=MODEL, max_tokens=1500, system=SYSTEM.format(cutoff=cutoff), tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": [b.model_dump() for b in r.content]})
        calls = [b for b in r.content if b.type == "tool_use"]
        if not calls:
            text = "".join(b.text for b in r.content if b.type == "text")
            m = re.search(r"\{.*\}", text, re.S)
            try:
                out = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                out = {}
            kept, dropped = check(out.get("sentences", []), results)
            return {"topic": out.get("topic"), "sentences": kept, "dropped": dropped, "caution": out.get("caution"),
                    "trace": trace, "model": MODEL, "cutoff": cutoff}
        tool_out = []
        for c in calls:
            res = getattr(box, c.name)(**c.input)
            results.append(res)
            trace.append({"tool": c.name, "input": c.input})
            tool_out.append({"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(res, ensure_ascii=False)})
        messages.append({"role": "user", "content": tool_out})
    return {"error": "정해진 횟수 안에 끝내지 못했다", "trace": trace}
