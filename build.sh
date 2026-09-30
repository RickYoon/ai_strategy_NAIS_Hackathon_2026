#!/bin/sh
# 배포용: 원본(data/raw)에서 계산 결과를 다시 만든다. LLM이 만든 파일(주제 이름 · 저장 응답 · 공백 지도)은 저장소에 있다.
set -e
pip install -r requirements.txt
python src/analyze.py ectc pkg pkg4 pvsc
python src/combo.py ectc pkg
python src/research.py
python src/learn.py
