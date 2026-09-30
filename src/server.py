"""화면과 계산 결과를 이어 주는 작은 서버 (표준 라이브러리만 사용).

사용: python src/server.py  →  http://127.0.0.1:8790
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
CACHE = {}


def derived(venue):
    if venue not in CACHE:
        CACHE[venue] = json.loads((ROOT / "data" / "derived" / f"{venue}.json").read_text(encoding="utf-8"))
    return CACHE[venue]


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
            return {"label": link["label"], "field": link["field"], "why": link["why"],
                    "collected_at": raw["collected_at"],
                    "series": {q: raw["series"][q] for q in link["queries"] if q in raw["series"]}}
    return None


class H(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json; charset=utf-8", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.send((ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/meta":
                d = derived(q.get("venue", "ectc"))
                top = sorted(((t, v["n"]) for t, v in d["topics"].items()), key=lambda x: -x[1])
                return self.send({k: d[k] for k in ("meta", "years", "total", "n_papers", "n_inst", "n_auth", "n_topics", "rules", "backtest")}
                                 | {"topic_names": [t for t, _ in top]})
            if u.path == "/api/topic":
                d = derived(q.get("venue", "ectc"))
                name = q.get("q", "").strip().lower()
                if name not in d["topics"]:
                    return self.send({"error": f"'{name}' 주제가 없다 (발표 {d['rules']['min_docs']}편 미만이거나 제목에 없는 말)"}, code=404)
                return self.send({"topic": name, **d["topics"][name], "events": events().get(name, []), "upstream": upstream(name)})
            if u.path == "/api/agent":
                import agent
                venue, c = q.get("venue", "ectc"), int(q.get("c", 2023))
                return self.send(agent.run(q.get("q", ""), c, derived(venue), events(), upstream))
            if u.path == "/api/has_llm":
                import agent
                return self.send({"llm": bool(agent.api_key()), "model": agent.MODEL})
            self.send({"error": "없는 주소"}, code=404)
        except FileNotFoundError as e:
            self.send({"error": f"파일 없음: {e.filename} — 먼저 collect.py, analyze.py 를 돌린다"}, code=500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("http://127.0.0.1:8790")
    ThreadingHTTPServer(("127.0.0.1", 8790), H).serve_forever()
