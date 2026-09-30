"""다른 분야의 흐름을 연도별 건수로 내려받는다 (OpenAlex, 제목 구절 검색).

사용: python src/upstream.py
입력: data/links.json (사람이 적은 연결 가설)
결과: data/raw/upstream.json
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.openalex.org/works"
CS_FIELD = 17  # OpenAlex 분야 번호: 컴퓨터 과학
YEARS = "2016-2025"  # 2026년은 아직 집계가 덜 되어 뺀다


def yearly(phrase):
    r = requests.get(API, params={
        "filter": f'title.search:"{phrase}",publication_year:{YEARS},primary_topic.field.id:{CS_FIELD}',
        "group_by": "publication_year"}, timeout=60)
    r.raise_for_status()
    got = {int(g["key"]): g["count"] for g in r.json()["group_by"]}
    return {str(y): got.get(y, 0) for y in range(2016, 2026)}


def main():
    links = json.loads((ROOT / "data" / "links.json").read_text(encoding="utf-8"))
    out = {"collected_at": datetime.now(timezone.utc).isoformat(), "source": "OpenAlex", "years": YEARS, "series": {}}
    for key, link in links.items():
        if key.startswith("_"):
            continue
        for q in link["queries"]:
            out["series"][q] = yearly(q)
            print(q, out["series"][q], flush=True)
            time.sleep(1)
    if not out["series"]:
        raise SystemExit("빈 결과 — 저장하지 않는다")
    (ROOT / "data" / "raw" / "upstream.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
