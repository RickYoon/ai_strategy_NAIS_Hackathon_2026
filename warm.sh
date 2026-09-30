#!/bin/zsh
# 시연 전에 한 번: 시연 질문들을 미리 돌려 응답을 저장해 둔다 (LLM이 끊겨도 저장된 응답이 나온다)
for q in "유리기판 연구를 시작할지 고민 중이야" "지금 뜨는 주제 찾아줘" "AI 때문에 달라지는 패키징 주제는?" "칩렛은 어느 학회에서 먼저 나왔고 누가 같이 하고 있어?" "glass" "glass core"; do
  echo "→ $q"
  curl -s -G --max-time 120 "http://127.0.0.1:8790/api/agent" --data-urlencode "q=$q" --data-urlencode "c=2026" --data-urlencode "fresh=1" | python3 -c "import sys,json;r=json.load(sys.stdin);print('   ', r.get('error') or ('저장됨 · '+r['view']+(' (저장된 응답)' if r.get('cached') else '')))"
done
