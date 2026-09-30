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


def key_info():
    """키가 어디서 왔고 모양이 맞는지. 키 자체는 돌려주지 않는다."""
    env = (os.environ.get("ANTHROPIC_API_KEY") or "").strip().strip('"').strip("'")
    f = Path.home() / ".config" / "anthropic" / "key"
    filed = f.read_text().strip() if f.exists() else ""
    for src, k in (("환경 변수", env), ("파일", filed)):
        if k:
            return {"source": src, "length": len(k), "starts_with_sk_ant": k.startswith("sk-ant-"),
                    "has_space": " " in k, "_key": k}
    return {"source": None, "_key": None}


def api_key():
    i = key_info()
    return i["_key"] if i.get("starts_with_sk_ant") and not i.get("has_space") else None


TOOLS = [
    {"name": "find_candidates", "description": "사용자가 주제를 정하지 않고 '무엇을 하면 좋을지' 물을 때 쓴다. 기준 연도에 내용 신호가 켜진 활발한 주제 목록을 돌려준다.",
     "input_schema": {"type": "object", "properties": {}}},
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
    {"name": "track_spread", "description": "이 주제가 학회마다 언제 처음 나타났고 언제 발표 비중 2%를 넘었는지 돌려준다(전파). 다섯 학회를 따로 센다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "find_relations", "description": "이 주제를 둘러싼 관계를 돌려준다: 어느 기관이 어느 학회에서 발표하는지, 어느 기관끼리 공동 발표하는지, 함께 나오는 세부 주제.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "cross_field", "description": "이 주제에 사람이 적어 둔 다른 분야 연결이 있으면, 그 분야의 연도별 논문 수를 돌려준다. 검증되지 않은 가설이다.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
]

SYSTEM = """너는 연구 방향을 정하는 사람을 돕는 에이전트다. 사용자의 말을 읽고, 스스로 계획을 세워 도구를 골라 부르고, 근거가 달린 문장으로 답한다.
규칙:
1. 지금은 {cutoff}년이라고 가정한다. 그 뒤의 일은 모른다. 네가 원래 알고 있는 지식으로 미래를 말하지 않는다.
2. 숫자는 도구 결과에 있는 것만 그대로 쓴다. 계산해서 새 숫자를 만들지 않는다.
3. 먼저 사용자가 무엇을 바라는지 셋 중 하나로 정한다.
   - "find": 주제를 정하지 않고 무엇이 뜨는지, 무엇을 하면 좋을지 묻는다 → find_candidates와 verify_signal을 부른다. 후보 이름은 도구가 돌려준 한국어 이름(korean_name)으로 말한다.
   - "cross": 다른 분야(예: AI, 소프트웨어)가 이 분야에 주는 영향을 묻는다 → cross_field("hbm")과 read_topic("hbm")을 부른다. 이 연결은 검증 전 가설이라고 반드시 말한다.
   - "topic": 특정 주제를 말한다 → search_topics로 주제 이름을 찾는다. 한글 주제는 영어 낱말 하나로 바꿔 찾는다(예: 유리기판 → glass). 찾은 것 가운데 발표가 가장 많은 넓은 주제 하나를 고르고, read_topic, industry_events, find_people, verify_signal을 부른다. 사용자가 전파 · 관계 · 협력 · 누가 하는지를 물으면 track_spread나 find_relations도 부른다.
4. 같은 도구를 같은 입력으로 두 번 부르지 않는다. 결과가 없으면 다른 낱말로 한 번 더 찾는다.
5. "성장한다", "유망하다"고 단정하지 않는다. 판정은 연구자가 한다. 산업 사건 목록이 비어 있으면 "산업 사건이 없다"고 하지 말고 "아직 적어 두지 않았다"고 말한다. 화면에 쓰는 말은 "신호가 켜진 활발한 주제" 대신 "이 방법이 고른 주제", "신호가 꺼진 주제" 대신 "고르지 않은 주제"를 쓴다.
6. 마지막에는 아래 JSON만 출력한다. "topic"에는 도구가 돌려준 주제 이름을 그대로 쓴다("find" · "cross"이면 null).
{{"view": "topic 또는 find 또는 cross", "topic": "주제 이름 또는 null", "sentences": [{{"text": "한국어 한 문장", "from": "근거가 된 도구 이름"}}], "caution": "이 근거의 한계 한 문장"}}
문장은 네 개에서 여섯 개. 한 문장에 사실 하나. 연도별 숫자를 늘어놓지 않는다. 과거 성적은 가장 최근 기준 연도의 것을 말하고, 신호가 없던 주제의 성적과 나란히 말한다."""


def profile(data, t, c):
    """후보 하나의 판단 재료. 점수가 아니라 사실만 적는다."""
    yrs, tot = data["years"], data["total"]

    def sh(a, b):
        idx = [i for i, y in enumerate(yrs) if a <= y <= b]
        s = sum(tot[i] for i in idx)
        return sum(t["count"][i] for i in idx) / s if s else 0.0

    recent, before = sh(c - 2, c), sh(yrs[0], c - 3)
    ratio = recent / before if before else None
    old = {n for p in t["papers"] if p["y"] <= c - 3 for n in p["inst"]}
    new = {n for p in t["papers"] if c - 2 <= p["y"] <= c for n in p["inst"]}
    j = t["judge"].get(str(c)) or {}
    return {"ratio": round(ratio, 2) if ratio else None,
            "trend": "새로 등장" if ratio is None else "오르는 중" if ratio >= 1.2 else "줄어드는 중" if ratio <= 0.8 else "비슷",
            "inst_recent": len(new), "inst_new": len(new - old),
            "inst_new_percent": round(len(new - old) / len(new) * 100) if new else 0,
            "rise_points": round((j.get("p_recent", 0) - j.get("p_before", 0)) * 100)}


def traits(data):
    """과거 세 시점의 후보를 모아, 실제로 커진 것과 아닌 것의 특징을 나란히 센다. 표본이 작다."""
    rows = []
    lab = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    for b in data["backtest"]:
        for x in b["lit_topics"]:
            t = data["topics"].get(x["topic"])
            if x["hot"] and t and lab.get(x["topic"], {"keep": True}).get("keep", True):
                rows.append({**profile(data, t, b["cutoff"]), "grew": x["grew"]})

    def split(label, f):
        a, o = [r for r in rows if f(r)], [r for r in rows if not f(r)]
        return {"label": label, "yes": [sum(r["grew"] for r in a), len(a)], "no": [sum(r["grew"] for r in o), len(o)]}

    return {"total": [sum(r["grew"] for r in rows), len(rows)], "splits": [
        split("발표 비중이 이미 오르는 중이었다", lambda r: r["trend"] == "오르는 중"),
        split("최근 3년 발표 기관의 70% 이상이 새로 들어온 곳이었다", lambda r: r["inst_new_percent"] >= 70),
        split("양산 문제 비중이 20%p 이상 올랐다", lambda r: r["rise_points"] >= 20)]}


def candidates(data, cutoff):
    """기준 연도에 내용 신호가 켜진 주제 목록. 전부 코드가 계산한다."""
    yrs = data["years"]
    idx = [i for i, y in enumerate(yrs) if cutoff - 2 <= y <= cutoff]
    tot = sum(data["total"][i] for i in idx) or 1
    elig = []
    for name, t in data["topics"].items():
        j = t["judge"].get(str(cutoff))
        if j:
            elig.append((name, t, j, sum(t["count"][i] for i in idx) / tot))
    elig.sort(key=lambda x: -x[3])
    hot = {n for n, *_ in elig[:int(len(elig) * 0.25)]}
    out = []
    for name, t, j, sh in elig:
        if j.get("lit"):
            out.append({"topic": name, "active": name in hot, "share_recent_percent": round(sh * 100, 1),
                        "problem_before_percent": round(j["p_before"] * 100), "problem_recent_percent": round(j["p_recent"] * 100),
                        "papers_recent": j["n_recent"], "share": [t["share"][i] for i, y in enumerate(yrs) if y <= cutoff],
                        **profile(data, t, cutoff)})
    out.sort(key=lambda x: (-x["active"], -(x["problem_recent_percent"] - x["problem_before_percent"])))
    return out


LABELS = ROOT / "data" / "derived" / "topic_labels.json"


def label_topics(names):
    """제목에서 뽑은 낱말 중 기술 주제가 아닌 일반 낱말을 LLM이 가려내고 한글 이름을 붙인다. 한 번 한 것은 남겨 둔다."""
    have = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    todo_all = [n for n in names if n not in have]
    key = api_key()
    for s in range(0, len(todo_all) if key else 0, 100):
        todo = todo_all[s:s + 100]
        import anthropic
        r = anthropic.Anthropic(api_key=key).messages.create(model=MODEL, max_tokens=8000, messages=[{"role": "user", "content":
            "아래는 반도체 패키징 · 전자부품 학회 발표 제목에서 뽑은 낱말이다. 각 낱말이 구체적인 기술 주제이면 keep=true, "
            "일반 낱말(예: challenges, demonstration, optimization, impact, system)이면 keep=false로 한다. "
            "keep=true이면 한국어 이름을 붙인다. 아래 JSON만 출력한다.\n"
            '{"낱말": {"keep": true, "ko": "한국어 이름"}}\n\n' + "\n".join(todo)}])
        m = re.search(r"\{.*\}", "".join(b.text for b in r.content if b.type == "text"), re.S)
        try:
            got = json.loads(m.group(0))
            have.update({k: v for k, v in got.items() if k in todo and isinstance(v, dict)})
            LABELS.parent.mkdir(parents=True, exist_ok=True)
            LABELS.write_text(json.dumps(have, ensure_ascii=False, indent=1), encoding="utf-8")
        except (AttributeError, json.JSONDecodeError):
            pass
    return have


def scorecard(data):
    """과거 성적표. 일반 낱말을 거른 뒤의 활발한 주제만 센다. 화면 · 에이전트 · 발표 자료가 모두 이 숫자를 쓴다."""
    names = [x["topic"] for b in data["backtest"] for x in b["lit_topics"] if x["hot"]] + [x["topic"] for b in data["backtest"] for x in b.get("unlit_hot", [])]
    lab = label_topics(sorted(set(names)))
    keep = lambda n: lab.get(n, {"keep": True}).get("keep", True)
    out = []
    for b in data["backtest"]:
        lit = [x for x in b["lit_topics"] if x["hot"] and keep(x["topic"])]
        un = [x for x in b.get("unlit_hot", []) if keep(x["topic"])]
        out.append({"cutoff": b["cutoff"], "opened": b["opened"], "lit": len(lit), "lit_grew": sum(x["grew"] for x in lit),
                    "unlit": len(un), "unlit_grew": sum(x["grew"] for x in un),
                    "lit_topics": [{"topic": x["topic"], "ko": lab.get(x["topic"], {}).get("ko") or x["topic"], "grew": x["grew"]} for x in lit]})
    return out


def describe_topics(names):
    """후보 주제가 무엇인지 한 문장으로 설명한다(LLM의 일반 지식). 숫자와 전망은 쓰지 않게 한다. 한 번 쓴 것은 남겨 둔다."""
    have = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    todo = [n for n in names if not have.get(n, {}).get("desc")]
    key = api_key()
    if todo and key:
        import anthropic
        r = anthropic.Anthropic(api_key=key).messages.create(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content":
            "아래는 반도체 패키징 학회 발표 제목에서 뽑은 기술 주제다. 각 주제가 무엇인지 비전공자도 알 수 있게 한국어 한 문장(40자 안팎)으로 설명한다. "
            "숫자, 전망, 평가(유망하다 등)는 쓰지 않는다. 아래 JSON만 출력한다.\n"
            '{"주제": "설명"}\n\n' + "\n".join(todo)}])
        m = re.search(r"\{.*\}", "".join(b.text for b in r.content if b.type == "text"), re.S)
        try:
            for k, v in json.loads(m.group(0)).items():
                if k in todo and isinstance(v, str):
                    have.setdefault(k, {"keep": True})["desc"] = v
            LABELS.write_text(json.dumps(have, ensure_ascii=False, indent=1), encoding="utf-8")
        except (AttributeError, json.JSONDecodeError):
            pass
    return have


class Toolbox:
    """도구 모음. 전부 기준 연도 가드를 거친다."""

    def __init__(self, data, events, upstream, cutoff, extra=None):
        self.d, self.ev, self.up, self.c = data, events, upstream, cutoff
        self.extra = extra or {}
        self.yi = [i for i, y in enumerate(data["years"]) if y <= cutoff]

    def find_candidates(self):
        c = [x for x in candidates(self.d, self.c) if x["active"]]
        lab = label_topics([x["topic"] for x in c])  # 일반 낱말은 뺀다
        keep = [x for x in c if lab.get(x["topic"], {"keep": True}).get("keep", True)]
        return [{"korean_name": lab.get(x["topic"], {}).get("ko"), **{k: v for k, v in x.items() if k != "share"}} for x in keep][:12]

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
        ev = [e for e in self.ev.get(topic.strip().lower(), []) if int(e["date"][:4]) <= self.c]
        return ev or {"recorded": 0, "note": "이 주제의 산업 사건은 아직 적어 두지 않았다. 사건 목록은 사람이 출처를 확인해 적는 것이라 비어 있을 뿐, 산업 사건이 없다는 뜻이 아니다."}

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
        return [{"cutoff": b["cutoff"], "opened": b["opened"], "active_topics_signal_on": b["lit"], "of_which_grew": b["lit_grew"],
                 "active_topics_signal_off": b["unlit"], "of_which_grew_off": b["unlit_grew"]}
                for b in scorecard(self.d) if b["cutoff"] <= self.c]

    def track_spread(self, topic):
        f = self.extra.get("spread")
        if not f:
            return {"note": "전파 도구를 쓸 수 없다"}
        r = f(topic.strip().lower())
        return [{"venue": v["venue"].upper(), "papers": v["papers"], "first_year": v["first_year"], "year_share_passed_2_percent": v["settled"]} for v in r["venues"]]

    def find_relations(self, topic):
        f = self.extra.get("relations")
        if not f:
            return {"note": "관계 도구를 쓸 수 없다"}
        g = f(topic.strip().lower())
        if "error" in g:
            return g
        return {"papers_in_five_venues": g["papers"], "institutions": g["totals"]["inst"], "authors": g["totals"]["auth"],
                "institution_at_venue": sorted(({"institution": e["b"], "venue": e["a"], "papers": e["n"]} for e in g["venue_inst"]), key=lambda x: -x["papers"])[:10],
                "joint_presentations": sorted(({"a": e["a"], "b": e["b"], "papers": e["n"]} for e in g["inst_inst"]), key=lambda x: -x["papers"])[:6],
                "sub_topics": [{"name": x["ko"], "papers": x["n"]} for x in g["topics"]]}

    def cross_field(self, topic):
        u = self.up(topic.strip().lower())
        if not u:
            return {"note": "이 주제에 적어 둔 다른 분야 연결이 없다"}
        return {"label": u["label"], "hypothesis": u["why"], "verified": False,
                "papers_by_year": {q: {y: n for y, n in s.items() if int(y) <= self.c} for q, s in u["series"].items()}}


def check(sentences, tool_results):
    """문장에 나온 숫자가 도구 결과에 실제로 있는지 본다. 없으면 버린다."""
    blob = json.dumps(tool_results, ensure_ascii=False)
    norm = lambda n: (n.lstrip("0") or "0") if "." not in n else n.rstrip("0").rstrip(".")
    have = {norm(n) for n in re.findall(r"\d+(?:\.\d+)?", blob.replace(",", ""))}
    kept, dropped = [], []
    for s in sentences:
        nums = re.findall(r"\d+(?:\.\d+)?", s["text"].replace(",", ""))
        bad = [n for n in nums if norm(n) not in have]
        (dropped if bad else kept).append({**s, **({"missing": bad} if bad else {})})
    return kept, dropped


def run(question, cutoff, data, events, upstream, extra=None):
    key = api_key()
    if not key:
        return {"error": "쓸 수 있는 LLM 키가 없다", "key": {k: v for k, v in key_info().items() if k != "_key"}}
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    box = Toolbox(data, events, upstream, cutoff, extra)
    messages = [{"role": "user", "content": question}]
    trace, results = [], []
    for _ in range(MAX_TURNS):
        r = client.messages.create(model=MODEL, max_tokens=2000, system=SYSTEM.format(cutoff=cutoff), tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": [
            {"type": "text", "text": b.text} if b.type == "text" else
            {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in r.content if b.type in ("text", "tool_use")]})
        calls = [b for b in r.content if b.type == "tool_use"]
        if not calls:
            text = "".join(b.text for b in r.content if b.type == "text")
            m = re.search(r"\{.*\}", text, re.S)
            try:
                out = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                out = {}
            kept, dropped = check(out.get("sentences", []), results)
            view = out.get("view") if out.get("view") in ("topic", "find", "cross") else "topic"
            if view == "topic" and not out.get("topic"):
                view = "find"
            return {"view": view, "topic": out.get("topic"), "sentences": kept, "dropped": dropped, "caution": out.get("caution"),
                    "trace": trace, "model": MODEL, "cutoff": cutoff}
        tool_out = []
        for c in calls:
            res = getattr(box, c.name)(**c.input)
            results.append(res)
            trace.append({"tool": c.name, "input": c.input})
            tool_out.append({"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(res, ensure_ascii=False)})
        messages.append({"role": "user", "content": tool_out})
    return {"error": "정해진 횟수 안에 끝내지 못했다", "trace": trace}
