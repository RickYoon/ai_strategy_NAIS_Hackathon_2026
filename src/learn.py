"""잘 커진 주제가 미리 어땠는지를 데이터에서 배워 본다 (연구용).

사람이 문턱값을 정하지 않는다. 주제마다 숫자 특징 16개를 재고, 로지스틱 회귀가 가중치를 배운다.
배운 것은 반드시 배우지 않은 자료로 시험한다:
  가) ECTC 2021 · 2022년으로 배우고 → ECTC 2023년으로 시험 (시간을 건너 시험)
  나) ECTC 전체로 배우고 → ECTC를 뺀 네 학회로 시험 (학회를 건너 시험)
  다) 목표를 '다른 네 학회에서 커졌나'로 바꿔 가)와 같이
점수: 위에서 20%로 꼽은 주제 중 실제로 커진 비율 (전체 비율과 견준다), AUC(0.5 = 동전 던지기).
사용: python src/learn.py
"""
import json
from collections import Counter
from pathlib import Path

import numpy as np


def fit_logistic(X, y, l2=3.0, steps=4000, lr=0.1):
    """가중치를 경사하강으로 배운다(작은 로지스틱 회귀, numpy만). 커진 주제가 드물어 두 쪽의 무게를 맞춘다."""
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    pos = max(1, y.sum())
    sw = np.where(y == 1, len(y) / (2 * pos), len(y) / (2 * max(1, len(y) - pos)))
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = (p - y) * sw
        w -= lr * (Z.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    predict = lambda Xn: 1 / (1 + np.exp(-(((Xn - mu) / sd) @ w + b)))
    predict.mu, predict.sd, predict.w, predict.b = mu, sd, w, b  # 화면에서 점수를 지표별로 쪼갤 때 쓴다
    return predict, w


def auc(y, p):
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))

import analyze
import combo
import research

ROOT = Path(__file__).resolve().parent.parent
FEATS = ["비중", "흐름(배수)", "가속(배수)", "양산 문제 비중", "양산 문제 비중 변화", "새 기관 비율", "기관 수", "기업 비율", "기업 비율 변화",
         "공동 발표 비율", "공동 발표 비율 변화", "한 기관 몫", "한 기관 몫 변화", "나라 수", "학회 수", "학회 수 변화"]


def feats(rows, idx, total, topic, c, vy, first):
    ps = [rows[k] for k in idx[topic]]
    rec, bef = [p for p in ps if c - 2 <= p[0] <= c], [p for p in ps if p[0] <= c - 3]
    if not rec or not bef:
        return None
    tot = lambda a, b: sum(total[y] for y in range(a, b + 1)) or 1
    sr, sb = len(rec) / tot(c - 2, c), len(bef) / tot(first, c - 3)
    sh = lambda y: sum(1 for p in ps if p[0] == y) / total[y] if total[y] else 0.0
    prev = (sh(c - 2) + sh(c - 1)) / 2
    fr = lambda g, f: sum(1 for p in g if f(p)) / len(g)
    prob = lambda p: bool(analyze.PROBLEM.search(" ".join(p[1])))
    comp = lambda p: any(tp == "company" for _, tp, _ in p[2])
    multi = lambda p: len({n for n, _, _ in p[2]}) >= 2
    inst = lambda g: {n for p in g for n, _, _ in p[2]}
    top1 = lambda g: max(Counter(n for p in g for n in {n for n, _, _ in p[2]}).values(), default=0) / len(g)
    vr = sum(1 for v, ys in vy[topic].items() if any(c - 2 <= y <= c for y in ys))
    vb = sum(1 for v, ys in vy[topic].items() if any(y <= c - 3 for y in ys))
    ir, ib = inst(rec), inst(bef)
    return [sr, sr / sb if sb else 0, sh(c) / prev if prev else 0, fr(rec, prob), fr(rec, prob) - fr(bef, prob),
            len(ir - ib) / len(ir) if ir else 0, len(ir), fr(rec, comp), fr(rec, comp) - fr(bef, comp),
            fr(rec, multi), fr(rec, multi) - fr(bef, multi), top1(rec), top1(rec) - top1(bef),
            len({co for p in rec for _, _, co in p[2] if co}), vr, vr - vb]


def dataset(key, labels, vy, cross=None):
    data = json.loads((ROOT / "data" / "derived" / f"{key}.json").read_text(encoding="utf-8"))
    rows, idx, total = research.raw_index(key)
    keep = lambda n: labels.get(n, {"keep": True}).get("keep", True)
    X, y, c_, names = [], [], [], []
    for b in data["backtest"]:
        hot = [(x["topic"], x["grew"]) for x in b["lit_topics"] if x["hot"]] + [(x["topic"], x["grew"]) for x in b["unlit_hot"]]
        for topic, grew in hot:
            if not keep(topic):
                continue
            f = feats(rows, idx, total, topic, b["cutoff"], vy, data["years"][0])
            if f is None:
                continue
            if cross is not None:
                grew = cross(topic, b["cutoff"])
                if grew is None:
                    continue
            X.append(f); y.append(int(grew)); c_.append(b["cutoff"]); names.append(topic)
    return np.array(X, float), np.array(y), np.array(c_), names


def test(name, Xtr, ytr, Xte, yte, nte=None):
    predict, w = fit_logistic(Xtr, ytr)
    p = predict(Xte)
    k = max(1, round(len(yte) * 0.2))
    top = np.argsort(-p)[:k]
    a = auc(yte, p)
    res = {"test": name, "train": [int(ytr.sum()), len(ytr)], "base": [int(yte.sum()), len(yte)],
           "top20": [int(yte[top].sum()), k], "auc": round(a, 2),
           "weights": sorted(zip(FEATS, [round(float(v), 2) for v in w]), key=lambda x: -abs(x[1]))[:6]}
    if nte is not None:
        res["top_names"] = [(nte[i], bool(yte[i])) for i in top[:12]]
    print(f"\n[{name}] 배운 자료 {res['train'][1]}개(커진 것 {res['train'][0]}) → 시험 {res['base'][1]}개(커진 것 {res['base'][0]}, {res['base'][0]/res['base'][1]:.0%})")
    print(f"   위 20%로 꼽은 {k}개 중 실제로 커진 것 {res['top20'][0]}개 ({res['top20'][0]/k:.0%}) · AUC {res['auc']}")
    print("   크게 본 특징:", ", ".join(f"{n} {w:+}" for n, w in res["weights"]))
    return res


if __name__ == "__main__":
    labels = json.loads((ROOT / "data" / "derived" / "topic_labels.json").read_text(encoding="utf-8"))
    vy = combo.venue_years()
    P = json.loads((ROOT / "data" / "derived" / "pkg4.json").read_text(encoding="utf-8"))
    yrs, tot = P["years"], P["total"]

    def cross(topic, c):
        t = P["topics"].get(topic)
        if not t or sum(t["count"][i] for i, y in enumerate(yrs) if y <= c) < 5:
            return None
        s = lambda a, b: sum(t["count"][i] for i, y in enumerate(yrs) if a <= y <= b) / (sum(tot[i] for i, y in enumerate(yrs) if a <= y <= b) or 1)
        a, b = s(c - 2, c), s(c + 1, c + 3)
        return a > 0 and b >= 1.5 * a

    Xe, ye, ce, ne = dataset("ectc", labels, vy)
    X4, y4, c4, n4 = dataset("pkg4", labels, vy)
    Xc, yc, cc, nc = dataset("ectc", labels, vy, cross)
    out = [test("가) 시간을 건너: ECTC 2021·22 → ECTC 2023", Xe[ce < 2023], ye[ce < 2023], Xe[ce == 2023], ye[ce == 2023], [n for n, c in zip(ne, ce) if c == 2023]),
           test("나) 학회를 건너: ECTC → 네 학회", Xe, ye, X4, y4),
           test("나') 거꾸로: 네 학회 → ECTC", X4, y4, Xe, ye),
           test("다) 전파 목표, 시간을 건너: ECTC 2021·22 → 2023 (네 학회에서 커졌나)", Xc[cc < 2023], yc[cc < 2023], Xc[cc == 2023], yc[cc == 2023], [n for n, c in zip(nc, cc) if c == 2023])]
    # 견줄 기준: 사람이 정한 네 지표의 개수로 줄 세운 것 (같은 시험 자료)
    def baseline(name, key, cut, cross_fn=None):
        data = json.loads((ROOT / "data" / "derived" / f"{key}.json").read_text(encoding="utf-8"))
        ys, ss = [], []
        for b in data["backtest"]:
            if cut is not None and b["cutoff"] != cut:
                continue
            hot = [(x["topic"], x["grew"]) for x in b["lit_topics"] if x["hot"]] + [(x["topic"], x["grew"]) for x in b["unlit_hot"]]
            for topic, grew in hot:
                if not labels.get(topic, {"keep": True}).get("keep", True) or topic not in data["topics"]:
                    continue
                if cross_fn is not None:
                    grew = cross_fn(topic, b["cutoff"])
                    if grew is None:
                        continue
                s = combo.signals(data, data["topics"][topic], topic, b["cutoff"], vy)
                ys.append(int(grew)); ss.append(sum(s) + 0.001 * data["topics"][topic]["n"] / 1000)
        ys, ss = np.array(ys), np.array(ss)
        k = max(1, round(len(ys) * 0.2)); top = np.argsort(-ss)[:k]
        print(f"   ↳ 비교 · 사람이 정한 네 지표 개수로 줄 세우면 [{name}]: 위 20% {k}개 중 {int(ys[top].sum())}개 ({ys[top].sum()/k:.0%}) · AUC {auc(ys, ss):.2f}")
        return {"test": "비교 · 네 지표 개수 · " + name, "top20": [int(ys[top].sum()), k], "auc": round(auc(ys, ss), 2)}

    out += [baseline("가) ECTC 2023", "ectc", 2023), baseline("나) 네 학회", "pkg4", None),
            baseline("다) ECTC 2023 → 네 학회에서 커졌나", "ectc", 2023, cross)]
    (ROOT / "data" / "derived" / "learn.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
