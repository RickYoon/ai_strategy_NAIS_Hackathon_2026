"""화면과 계산 결과를 이어 주는 작은 서버 (표준 라이브러리만 사용).

사용: python src/server.py  →  http://127.0.0.1:8790
주소별 처리는 src/api.py 에 있고, 요청마다 다시 읽는다.
"""
import importlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
CACHE = {}


def derived(venue):
    """계산 결과. 파일이 바뀌면 다시 읽는다."""
    f = ROOT / "data" / "derived" / f"{venue}.json"
    m = f.stat().st_mtime
    if venue not in CACHE or CACHE[venue][0] != m:
        CACHE[venue] = (m, json.loads(f.read_text(encoding="utf-8")))
    return CACHE[venue][1]


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
            import api
            importlib.reload(api)
            body, code = api.handle(u.path, q, derived)
            self.send(body, code=code)
        except FileNotFoundError as e:
            self.send({"error": f"파일 없음: {e.filename} — 먼저 collect.py, analyze.py 를 돌린다"}, code=500)
        except Exception as e:  # 화면에 이유를 보여준다
            self.send({"error": f"{type(e).__name__}: {str(e)[:400]}"}, code=500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "8790"))
    print(f"http://127.0.0.1:{port}")
    host = os.environ.get("HOST", "127.0.0.1")  # 배포(Render)에서는 HOST=0.0.0.0
    ThreadingHTTPServer((host, port), H).serve_forever()
