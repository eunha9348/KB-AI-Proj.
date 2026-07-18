"""
risk_engine.py
청년·대학생 주택 안전진단 위험도 산출 엔진.

핵심 아이디어 (기획서 3번 기능: 종합 계약 위험도 %)
  - 전세가율(jeonse_ratio)      = 보증금 / 추정매매가
  - 선순위채권비율(senior_debt) = (근저당설정액 + 보증금) / 추정매매가
      → 1.0 을 넘으면 경매 시 보증금 전액 회수가 불가능한 '깡통전세' 신호
  - 위반건축물 여부는 가산 위험

산출된 0~100 점을 4단계 등급으로 매핑하고, 등급별 은행 금융상품을 추천한다.
모든 입력값은 공공/제휴 데이터에서 나온 값이라는 전제(무단 크롤링 배제).
"""

from dataclasses import dataclass


@dataclass
class RiskInput:
    sale_price: int        # 추정 매매 시세 (만원)
    deposit: int           # 전세 보증금 (만원)
    mortgage_amount: int   # 근저당 설정액 (만원)
    is_illegal: bool = False


@dataclass
class RiskResult:
    jeonse_ratio: float
    senior_debt_ratio: float
    risk_score: int
    risk_grade: str
    recommended_product: str


# 위험등급 임계치 (score 기준)
GRADE_BANDS = [
    (0, 30, "안전"),
    (30, 55, "주의"),
    (55, 80, "경고"),
    (80, 101, "위험"),
]

# 등급 → 추천 금융상품 코드 (finance_products.product_code 와 연결)
PRODUCT_BY_GRADE = {
    "안전": "PREMIUM_LOAN",   # 안전 매물 우대금리 전월세 대출
    "주의": "STD_LOAN",       # 일반 전월세 대출
    "경고": "HUG_INSURANCE",  # 보증보험 필수 권고
    "위험": "REJECT_OR_HUG",  # 인수 곤란 / 보증보험 가입 시에만 검토
}


def _grade_of(score: int) -> str:
    for low, high, grade in GRADE_BANDS:
        if low <= score < high:
            return grade
    return "위험"


def assess(inp: RiskInput) -> RiskResult:
    """위험도 0~100 산출. 높을수록 위험."""
    sale = max(inp.sale_price, 1)  # 0 나눗셈 방지
    jeonse_ratio = round(inp.deposit / sale, 4)
    senior_debt_ratio = round((inp.mortgage_amount + inp.deposit) / sale, 4)

    # 1) 선순위채권비율 기반 기본 점수 (0.5 이하는 매우 안전, 1.0 이상은 최고위험)
    #    0.5 → 0점, 1.0 → 70점 구간을 선형 매핑, 그 이상은 급증
    if senior_debt_ratio <= 0.5:
        base = 0
    elif senior_debt_ratio <= 1.0:
        base = (senior_debt_ratio - 0.5) / 0.5 * 70
    else:
        base = 70 + min((senior_debt_ratio - 1.0) / 0.3, 1.0) * 30  # 1.3배 이상은 100

    # 2) 전세가율 가산 (0.8 초과분에 대해 최대 15점)
    if jeonse_ratio > 0.8:
        base += min((jeonse_ratio - 0.8) / 0.2, 1.0) * 15

    # 3) 위반건축물 가산
    if inp.is_illegal:
        base += 12

    score = int(round(min(base, 100)))
    grade = _grade_of(score)
    return RiskResult(
        jeonse_ratio=jeonse_ratio,
        senior_debt_ratio=senior_debt_ratio,
        risk_score=score,
        risk_grade=grade,
        recommended_product=PRODUCT_BY_GRADE[grade],
    )


if __name__ == "__main__":
    # 간단한 자체 점검
    samples = [
        RiskInput(sale_price=30000, deposit=12000, mortgage_amount=3000),   # 안전
        RiskInput(sale_price=25000, deposit=20000, mortgage_amount=4000),   # 경고~위험
        RiskInput(sale_price=20000, deposit=19000, mortgage_amount=8000),   # 위험(깡통)
    ]
    for s in samples:
        r = assess(s)
        print(f"매매 {s.sale_price} / 보증금 {s.deposit} / 근저당 {s.mortgage_amount} "
              f"=> 전세가율 {r.jeonse_ratio:.0%}, 선순위 {r.senior_debt_ratio:.0%}, "
              f"위험도 {r.risk_score}점 [{r.risk_grade}] → {r.recommended_product}")
