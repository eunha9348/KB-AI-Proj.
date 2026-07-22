"""
risk_engine.py — 청년·대학생 주택 안전진단 위험도 산출 엔진 (금융지표 + 뉴스감성 결합)

━━━ 2단 구조 (fundamentals dominate, context modulates) ━━━
최종 위험도 = 100 × ( W_FUND·f + W_CONTEXT·c )   (W_FUND=0.78, W_CONTEXT=0.22)

  f = 매물 고유 '금융·물건 펀더멘털' 위험 (0~1)  ← 계약 당사자에게 직접 귀속되는 hard fact
      · 선순위채권비율(가장 큰 비중): (근저당+보증금)/매매가. 1.0 초과 시 경매 배당으로
        보증금 전액 회수 불가('깡통전세').
      · 전세가율: 보증금/매매가. 0.8 초과분 가산.
      · 위반건축물(건축물대장): 보증가입·경매배당 불리 → 가산.
      · 주거유형: 비아파트(오피스텔·다세대·빌라)는 전세사기·역전세가 집중된 실증적 근거
        (2026 전세사기 피해자 3.6만명 중 비아파트·청년 밀집) 반영해 소폭 가산.
      · 건물 노후도: 준공 후 경과연수. 노후 비아파트는 담보가치·환금성 저하.

  c = 지역 '시장·심리 컨텍스트' 위험 (0~1)  ← 개별 물건이 아니라 지역 환경에서 오는 위험
      · 전세가 변동성(CV = 전세가 표준편차/평균, 실거래가로 산출): 높을수록 역전세 위험.
      · 뉴스 감성 '초과분'(news_sentiment.py, 2-gram 위험 렉시콘 × 실제 기사 코퍼스):
        서울 전체 기준선을 모든 자치구에 균일하게 더하면 위험도를 일괄 상향시켜 '조절'이
        아니라 '가산'이 되어 버린다(검증 E에서 확인됨). 따라서 감성은 서울 기준선을 '초과'
        하는 만큼만 위험에 반영한다: sentiment_excess = (감성−기준선)/(1−기준선). 이렇게 하면
        전세사기 보도가 집중된 자치구(관악·강서 등)만 차등 가산되고, 평범한 자치구는 0 이다.

설계 의도(정직성):
  · 펀더멘털 f 가 지배적(78%)이라, 뉴스 감성만으로 근본적으로 안전한 물건이 '위험'으로
    뒤집히지 않는다 — 감성은 조절 요소일 뿐이다. 반대로 깡통전세는 감성과 무관하게 위험.
  · 모든 가중치·임계값은 아래 상수로 노출하고 docs/risk_methodology_validation.md 에서
    민감도·시나리오로 검증한다. 임의의 결과를 지어내지 않고 입력→출력이 재현 가능하다.
"""

from dataclasses import dataclass, field

# ── 상단 결합 가중치 ─────────────────────────────────────────────
W_FUND = 0.78       # 매물 펀더멘털 비중(지배적)
W_CONTEXT = 0.22    # 지역 컨텍스트(변동성+뉴스감성) 비중

# ── 컨텍스트 내부 가중치 ─────────────────────────────────────────
C_VOL = 0.45        # 전세가 변동성 비중
C_SENTIMENT = 0.55  # 뉴스 감성 비중
VOL_CV_CAP = 0.60   # 변동계수(CV) 이 값 이상이면 변동성 위험 1.0 으로 포화

# ── 펀더멘털 가산치 ──────────────────────────────────────────────
JEONSE_PREMIUM_MAX = 0.12    # 전세가율 0.8→1.0 구간 최대 가산
ILLEGAL_PREMIUM = 0.12       # 위반건축물 가산
BUILDING_TYPE_RISK = {       # 주거유형 위험 가산(비아파트 전세사기 집중 반영)
    "아파트": 0.00,
    "오피스텔": 0.04,
    "도시형생활주택": 0.05,
    "다세대": 0.06,
    "빌라": 0.07,
}
AGE_PREMIUM_MAX = 0.05       # 노후도 최대 가산
AGE_FULL_YEARS = 25          # 25년 이상이면 노후 가산 최대
REFERENCE_YEAR = 2026        # 노후도 계산 기준연도(재현성 위해 고정)

# 위험등급 임계치 (score 기준)
GRADE_BANDS = [
    (0, 30, "안전"),
    (30, 55, "주의"),
    (55, 80, "경고"),
    (80, 101, "위험"),
]


@dataclass
class RiskInput:
    sale_price: int              # 추정 매매 시세 (만원)
    deposit: int                 # 전세 보증금 (만원)
    mortgage_amount: int         # 근저당 설정액 (만원)
    is_illegal: bool = False
    building_type: str = "아파트"
    build_year: int = None
    district_jeonse_cv: float = 0.0     # 지역 전세가 변동계수(표준편차/평균)
    district_sentiment: float = 0.0     # 지역 뉴스 감성지수 0~1 (news_sentiment)
    city_sentiment_baseline: float = 0.0  # 서울 감성 기준선(초과분만 위험에 반영)


@dataclass
class RiskResult:
    jeonse_ratio: float
    senior_debt_ratio: float
    risk_score: int
    risk_grade: str
    # 투명성: 세부 요소(프론트/검증에서 분해 표시)
    fundamental_score: float = 0.0      # f (0~1)
    context_score: float = 0.0          # c (0~1)
    components: dict = field(default_factory=dict)


def _grade_of(score: int) -> str:
    for low, high, grade in GRADE_BANDS:
        if low <= score < high:
            return grade
    return "위험"


def _fundamental(inp: RiskInput, sale: int, jeonse_ratio: float,
                 senior_debt_ratio: float):
    """매물 고유 펀더멘털 위험 f(0~1)와 세부 기여도를 산출."""
    # 1) 선순위채권비율 기반 기본치 (0.5 이하 안전, 1.0 이상 최고위험)
    if senior_debt_ratio <= 0.5:
        base = 0.0
    elif senior_debt_ratio <= 1.0:
        base = (senior_debt_ratio - 0.5) / 0.5 * 0.85
    else:
        base = 0.85 + min((senior_debt_ratio - 1.0) / 0.3, 1.0) * 0.15

    # 2) 전세가율 가산 (0.8 초과분)
    jeonse_prem = 0.0
    if jeonse_ratio > 0.8:
        jeonse_prem = min((jeonse_ratio - 0.8) / 0.2, 1.0) * JEONSE_PREMIUM_MAX

    # 3) 위반건축물
    illegal_prem = ILLEGAL_PREMIUM if inp.is_illegal else 0.0

    # 4) 주거유형
    type_prem = BUILDING_TYPE_RISK.get(inp.building_type, 0.03)

    # 5) 노후도
    age_prem = 0.0
    if inp.build_year:
        age = max(REFERENCE_YEAR - int(inp.build_year), 0)
        age_prem = min(age / AGE_FULL_YEARS, 1.0) * AGE_PREMIUM_MAX

    f = min(base + jeonse_prem + illegal_prem + type_prem + age_prem, 1.0)
    return f, {
        "senior_debt_base": round(base, 4),
        "jeonse_premium": round(jeonse_prem, 4),
        "illegal_premium": illegal_prem,
        "building_type_premium": type_prem,
        "age_premium": round(age_prem, 4),
    }


def _context(inp: RiskInput):
    """지역 컨텍스트 위험 c(0~1): 전세가 변동성 + 뉴스 감성 '초과분'.
    감성은 서울 기준선을 초과하는 만큼만 반영해 자치구 차등 신호로 작동시킨다."""
    vol = min(max(inp.district_jeonse_cv, 0.0) / VOL_CV_CAP, 1.0)
    base = min(max(inp.city_sentiment_baseline, 0.0), 0.999)
    sent = min(max(inp.district_sentiment, 0.0), 1.0)
    sent_excess = max(sent - base, 0.0) / (1.0 - base) if base < 1.0 else 0.0
    sent_excess = min(sent_excess, 1.0)
    c = min(C_VOL * vol + C_SENTIMENT * sent_excess, 1.0)
    return c, {"volatility_norm": round(vol, 4),
               "sentiment": round(sent, 4),
               "sentiment_excess": round(sent_excess, 4)}


def assess(inp: RiskInput) -> RiskResult:
    """위험도 0~100 산출. 높을수록 위험. 펀더멘털(78%)+컨텍스트(22%) 결합."""
    sale = max(inp.sale_price, 1)  # 0 나눗셈 방지
    jeonse_ratio = round(inp.deposit / sale, 4)
    senior_debt_ratio = round((inp.mortgage_amount + inp.deposit) / sale, 4)

    f, f_parts = _fundamental(inp, sale, jeonse_ratio, senior_debt_ratio)
    c, c_parts = _context(inp)

    score = int(round(min(100 * (W_FUND * f + W_CONTEXT * c), 100)))
    grade = _grade_of(score)
    return RiskResult(
        jeonse_ratio=jeonse_ratio,
        senior_debt_ratio=senior_debt_ratio,
        risk_score=score,
        risk_grade=grade,
        fundamental_score=round(f, 4),
        context_score=round(c, 4),
        components={**f_parts, **c_parts,
                    "w_fund": W_FUND, "w_context": W_CONTEXT},
    )


if __name__ == "__main__":
    from kb_products import match_product

    BASE = 0.47  # 서울 감성 기준선(예시)
    samples = [
        ("안전 아파트/저채권", RiskInput(30000, 12000, 3000, building_type="아파트",
                                    build_year=2015, district_jeonse_cv=0.2,
                                    district_sentiment=0.47, city_sentiment_baseline=BASE)),
        ("경고 빌라/높은채권/관악감성", RiskInput(25000, 20000, 4000, building_type="빌라",
                                    build_year=2005, district_jeonse_cv=0.5,
                                    district_sentiment=0.79, city_sentiment_baseline=BASE)),
        ("위험 깡통전세", RiskInput(20000, 19000, 8000, building_type="다세대",
                                build_year=2000, is_illegal=True,
                                district_jeonse_cv=0.55, district_sentiment=0.79,
                                city_sentiment_baseline=BASE)),
    ]
    for label, s in samples:
        r = assess(s)
        m = match_product(r.risk_score, r.risk_grade, r.jeonse_ratio,
                          r.senior_debt_ratio, s.deposit)
        print(f"[{label}] 전세가율 {r.jeonse_ratio:.0%} 선순위 {r.senior_debt_ratio:.0%} "
              f"| f={r.fundamental_score} c={r.context_score} → 위험도 {r.risk_score}점 "
              f"[{r.risk_grade}] → {m.product_code}")
