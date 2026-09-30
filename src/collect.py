"""OpenAlex 공개 API에서 학회 발표 기록을 내려받는다.

사용: python src/collect.py ectc
결과: data/raw/<학회>.jsonl (논문 한 줄에 하나), data/raw/<학회>.meta.json (수집 시각·건수)
요청은 한 번에 하나씩 보낸다.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.openalex.org/works"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")  # 있으면 OpenAlex 우선 처리 대상이 된다
# 학회 = IEEE DOI 앞부분
VENUES = {
    "ectc": {"name": "ECTC (전자부품기술학회)", "doi": "10.1109/ectc", "field": "반도체 패키징"},
    "pvsc": {"name": "PVSC (태양광 전문가 학회)", "doi": "10.1109/pvsc", "field": "태양광"},
    "iedm": {"name": "IEDM (국제전자소자회의)", "doi": "10.1109/iedm", "field": "반도체 소자"},
    "irps": {"name": "IRPS (신뢰성 물리 심포지엄)", "doi": "10.1109/irps", "field": "반도체 신뢰성"},
    # 검증용: 같은 학회를 더 옛날부터 (과거 시점 검증에 비교할 기록을 늘리려고)
    "ectc_long": {"name": "ECTC 2010~2026 (검증용 긴 기록)", "doi": "10.1109/ectc", "field": "반도체 패키징", "years": "2010-2026"},
    "eptc": {"name": "EPTC (전자패키징기술학회)", "doi": "10.1109/eptc", "field": "반도체 패키징"},
    "icept": {"name": "ICEPT (전자패키징기술 국제학회)", "doi": "10.1109/icept", "field": "반도체 패키징"},
    "estc": {"name": "ESTC (전자시스템통합기술학회)", "doi": "10.1109/estc", "field": "반도체 패키징"},
    "itherm": {"name": "ITherm (열 · 열기계 현상 학회)", "doi": "10.1109/itherm", "field": "패키징 열 설계"},
}
YEARS = "2018-2026"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"


def slim(w):
    """필요한 칸만 남긴다."""
    auths = []
    for a in w.get("authorships", []):
        auths.append({
            "id": (a.get("author") or {}).get("id"),
            "name": (a.get("author") or {}).get("display_name"),
            "inst": [{"id": i.get("id"), "name": i.get("display_name"), "country": i.get("country_code"),
                      "type": i.get("type")} for i in a.get("institutions", [])],
        })
    return {
        "id": w["id"], "doi": w.get("doi"), "title": w.get("title") or "",
        "year": w.get("publication_year"), "has_abstract": bool(w.get("abstract_inverted_index")),
        "cited": w.get("cited_by_count", 0), "authors": auths,
    }


def collect(key):
    v = VENUES[key]
    OUT.mkdir(parents=True, exist_ok=True)
    rows, cursor = [], "*"
    while cursor:
        r = requests.get(API, params={
            "filter": f"doi_starts_with:{v['doi']},publication_year:{v.get('years', YEARS)}",
            "select": "id,doi,title,publication_year,abstract_inverted_index,cited_by_count,authorships",
            "per-page": 200, "cursor": cursor, **({"mailto": MAILTO} if MAILTO else {})}, timeout=60)
        r.raise_for_status()
        d = r.json()
        if not d["results"]:
            break
        rows += [slim(w) for w in d["results"]]
        cursor = d["meta"].get("next_cursor")
        print(f"  {key}: {len(rows)} / {d['meta']['count']}", flush=True)
        time.sleep(1)
    if not rows:
        sys.exit("빈 결과 — 저장하지 않는다")
    with open(OUT / f"{key}.jsonl", "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    meta = {"venue": key, "name": v["name"], "field": v["field"], "source": "OpenAlex", "doi_prefix": v["doi"],
            "years": v.get("years", YEARS), "count": len(rows), "collected_at": datetime.now(timezone.utc).isoformat()}
    (OUT / f"{key}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장:", OUT / f"{key}.jsonl", len(rows), "건")


if __name__ == "__main__":
    for k in sys.argv[1:] or ["ectc"]:
        collect(k)
