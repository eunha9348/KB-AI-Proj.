"""
news_sentiment.py — 전세시장 뉴스 감성분석 엔진 (2-gram 위험 렉시콘 기반)

목적
  기존 금융지표(전세가율·선순위채권비율 등)만으로는 잡히지 않는 '시장 심리 위축·
  뉴스 리스크 압력'을 정량화해 위험도 산출에 결합한다. 예: 특정 자치구에 전세사기·
  깡통전세 보도가 집중되면 그 지역 계약의 심리적·실질적 위험이 커진다.

방법 (2-gram 알고리즘)
  1) 렉시콘: 전세 위험과 직접 연관된 2-gram(두 토큰) 위험어에 심각도 weight(1~3)를 부여
     (data/news_sentiment.json 의 lexicon_2gram). weight 는 방법론적 편집 판단이며 문서에
     근거를 남긴다(수치를 지어낸 것이 아니라 '가중치 설계'임).
  2) 코퍼스: 실제로 존재하는 언론 보도(제목 verbatim + 확인된 사실 + URL). 각 기사 텍스트에서
     렉시콘 2-gram 의 등장 여부를 실제 문자열 매칭으로 검출한다(공백 제거 후 부분일치 →
     띄어쓰기에 강건). 이 매칭은 실행 시점에 코드가 수행하므로 검증 가능하다.
  3) 도시(서울) 위험지수: risk_mass = Σ(weight × 문서빈도df). 포화함수로 0~1 로 정규화한다.
       city_index = 1 - exp(-risk_mass / SCALE)   (SCALE 은 문서화된 상수)
     easing(완화) 신호 기사(매물 증가 등)는 소폭 감산한다.
  4) 자치구 지수: 실명 근거가 있는 자치구만 city_index 위에 신호(level)를 가산한다.
       score = base + (1-base) × level × DISTRICT_GAIN
     근거 없는 자치구는 city_index 를 그대로 상속한다(정직성: 자치구별 수치를 지어내지 않음).

⚠️ 허구 금지: 코퍼스의 모든 항목은 실제 기사이며 URL 로 검증 가능하다. 감성 점수는 이 실제
   코퍼스에 대한 결정론적 계산 결과이지, 임의로 지정한 값이 아니다.
"""

import json
import math
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "news_sentiment.json"

# --- 문서화된 정규화 상수 ---------------------------------------------------
# SCALE: risk_mass 를 0~1 로 포화시키는 척도.
#   [보정 근거] 위험 코퍼스는 '전세사기' 주제를 겨냥해 수집되므로 해당 2-gram 이 다수 기사에
#   반복 등장(선택편향)한다. city_index 를 문자 그대로 '전체 뉴스의 부정 비율'로 읽으면 과대
#   해석된다. 따라서 SCALE 을 크게 잡아 현재 코퍼스가 도시 기준선 ≈0.5 로 매핑되게 보정한다.
#   이렇게 하면 (a) 감성 요소가 금융 펀더멘털을 압도하지 않고 '조절 요소'로 작동하고,
#   (b) 자치구 실명 근거에 따른 차별화 여지(0.5→최대 ~0.8)가 확보된다. validate_risk.py 로 재현.
SCALE = 48.0
# EASING_STEP: easing(완화) 신호 기사 1건당 city_index 감산량(비율)
EASING_STEP = 0.04
# DISTRICT_GAIN: 자치구 실명 근거 신호가 도시 기준선 위로 끌어올리는 최대 비중
DISTRICT_GAIN = 0.6

_CACHE = None


def load():
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return _CACHE


def _despace(s: str) -> str:
    return "".join((s or "").split())


def _article_text(a: dict) -> str:
    return f"{a.get('title', '')} {a.get('facts', '')}"


def _bigram_key(term: str) -> str:
    """'전세 사기' → '전세사기' (공백 제거 부분일치용). 2-gram 의 두 토큰 연접 형태."""
    return _despace(term)


def analyze():
    """코퍼스 전체에 대한 2-gram 매칭 결과 + 도시 위험지수를 계산한다(결정론적)."""
    data = load()
    lex = data["lexicon_2gram"]
    corpus = data["corpus"]

    # 기사별 despaced 텍스트
    docs = [(_despace(_article_text(a)), a) for a in corpus]

    term_stats = []
    risk_mass = 0.0
    for entry in lex:
        term = entry["term"]
        key = _bigram_key(term)
        hits = [a for despaced, a in docs if key in despaced]
        df = len(hits)
        term_stats.append({
            "term": term, "weight": entry["weight"],
            "polarity": entry["polarity"], "df": df,
            "articles": [h["url"] for h in hits],
        })
        if entry["polarity"] == "risk":
            risk_mass += entry["weight"] * df

    # easing(완화) 신호 기사 수
    n_easing = sum(1 for a in corpus if a.get("polarity_hint") == "easing")

    city_index = 1.0 - math.exp(-risk_mass / SCALE)
    city_index = max(0.0, city_index - EASING_STEP * n_easing)
    city_index = round(min(city_index, 1.0), 4)

    return {
        "city_index": city_index,
        "risk_mass": round(risk_mass, 2),
        "n_articles": len(corpus),
        "n_easing": n_easing,
        "terms": sorted(term_stats, key=lambda t: -t["weight"] * t["df"]),
    }


def district_sentiment(name: str):
    """자치구명 → 감성 위험 점수(0~1) + 근거. 근거 없으면 도시 기준선 상속."""
    data = load()
    base = analyze()["city_index"]
    sig = data.get("district_signals", {}).get(name)
    if not sig:
        return {"name": name, "score": base, "base_city": base,
                "signal_level": 0.0, "evidence": [], "inherited": True,
                "note": "실명 근거 없음 → 서울 기준선 상속"}
    level = float(sig.get("level", 0.0))
    score = base + (1.0 - base) * level * DISTRICT_GAIN
    return {"name": name, "score": round(min(score, 1.0), 4), "base_city": base,
            "signal_level": level, "evidence": sig.get("evidence", []),
            "inherited": False, "note": sig.get("note", "")}


def all_district_scores(names):
    return {n: district_sentiment(n) for n in names}


if __name__ == "__main__":
    a = analyze()
    print(f"[news] 기사 {a['n_articles']}건, risk_mass={a['risk_mass']}, "
          f"완화신호 {a['n_easing']}건 → 서울 city_index={a['city_index']}")
    print("[news] 상위 2-gram 위험어(가중×빈도):")
    for t in a["terms"][:8]:
        if t["df"]:
            print(f"   - {t['term']:8s} w{t['weight']} × df{t['df']}  {t['articles']}")
    print("[news] 자치구 감성 예시:")
    for d in ["관악구", "강서구", "구로구", "노원구", "성동구"]:
        s = district_sentiment(d)
        tag = "상속" if s["inherited"] else f"근거{len(s['evidence'])}건"
        print(f"   - {d}: score={s['score']} ({tag}) {s['note']}")
