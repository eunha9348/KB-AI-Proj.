"""
kb_products.py — KB국민은행 실제 금융상품 마스터 + 위험도 기반 추천 알고리즘

⚠️ 주의: 아래 금리·한도는 KB국민은행 공식 상품안내 채널(kbthink.com 등)에 공개된 정보를
기준으로 한 "참고값"입니다. 실제 적용금리는 신용도·COFIX 변동·정책 개정에 따라 달라지므로,
실서비스 전환 시 KB국민은행 API/영업점을 통한 실시간 조회로 대체해야 합니다. 존재하지 않는
상품이나 임의의 수치를 만들어내지 않기 위해, 확정 정보가 없는 항목(예: KB 청년 맞춤형
전세자금대출의 COFIX 연동 변동금리)은 수치 대신 설명 텍스트로 남겨두었습니다.

reference_url은 로그인 세션이 필요한 내부뱅킹 딥링크(obank.kbstar.com/quics?...) 대신,
세션 없이 항상 열리는 각 상품별 공식 안내 페이지(kbthink.com의 상품 가이드, 또는
주택도시보증공사 HUG의 해당 보증상품 소개 페이지)로 연결한다. 도메인 루트가 아니라
"해당 상품 페이지로 바로 이동"이 목표이므로, 상품별로 서로 다른 세부 경로를 사용한다.

매칭 알고리즘 (match_product) — 위험등급뿐 아니라 선순위채권비율(연속값)과 보증금 규모까지
결합한 다요소 매칭으로, 동일 등급 안에서도 실제 KB 상품 6종 중 가장 적합한 상품을 고른다.
  0) 보증금이 HUG·HF 보증형 KB스타 전세자금대출의 최대한도(채권보전조치 포함 4.44억원)를
     초과 → 위험등급과 무관하게 한도 제한이 사실상 없는 서울보증보험(SGI) 연계
     'KB스타 전세자금대출(SGI)'만 실제로 커버 가능하므로 최우선 매칭한다(단, '위험'
     등급은 0)보다 보증보험 가입 확인이 항상 우선이므로 예외로 둔다).
  1) 위험등급 '위험' → 대출 매칭을 보류하고 HUG 전세보증금반환보증 가입을 최우선 안내
  2) 위험등급 '경고' → 선순위채권비율(연속값)로 다시 세분화한다.
     - 90% 이상: 반환보증이 결합된 'KB스타 전세자금대출(HUG)' (안전장치 최우선)
     - 90% 미만: 보증료가 더 저렴한 'KB스타 전세자금대출(HF)' (동일 계열 상품 중
       고객 부담 비용을 낮추는 최적화)
  3) 위험등급 '안전'/'주의' → 보증금이 청년전용 버팀목 전세자금대출의 한도 이내면
     정부 정책자금(최저금리)을 우선 매칭하고, 한도를 초과하면 KB 자체상품(청년 맞춤형
     전세자금대출)을 매칭한다.
  4) '안전' 등급이 KB 자체상품(청년 맞춤형/HF/HUG/SGI 중 하나)으로 매칭된 경우에 한해,
     이 플랫폼이 제안하는 안전매물 특별 우대(PLATFORM_SAFE_BONUS)를 추가 적용한다.
     이는 KB의 공시금리가 아니라 본 제안이 신설을 제안하는 차등금리 정책이다.
"""

from dataclasses import dataclass

# 청년전용 버팀목 전세자금대출의 보증금 한도(수도권 일반 기준, 만원).
# 세대 유형(단독세대주 등)에 따라 상이할 수 있어 대표값을 사용한다.
BEOTIMOK_DEPOSIT_LIMIT_MANWON = 30000

# HUG·HF 보증형 KB스타 전세자금대출의 최대한도(채권보전조치 시 4억4천4백만원, 공개자료 기준).
# 이를 초과하는 고액 전세는 한도 제한이 사실상 없는 SGI 연계 상품만 커버 가능하다.
HF_HUG_MAX_COVERAGE_MANWON = 44400

# 경고 등급 내에서 HUG 반환보증 결합(KB스타 HUG) vs 저비용 HF 보증(KB스타 HF)을
# 가르는 선순위채권비율 임계값. 이 값 이상이면 반환보증 결합이 안전장치로 필요하다고 본다.
WARNING_HUG_COMBO_THRESHOLD = 0.90

# 이 플랫폼이 제안하는 안전매물 특별 우대금리(%p). KB 공시금리가 아닌 '제안' 정책.
PLATFORM_SAFE_BONUS = -0.3

PRODUCTS = {
    "BEOTIMOK_YOUTH": dict(
        product_code="BEOTIMOK_YOUTH",
        name="청년전용 버팀목 전세자금대출",
        provider="주택도시기금 (KB국민은행 등 수탁은행 취급)",
        product_type="정책대출",
        guarantee_agency="주택도시보증공사(HUG) 협약",
        rate_min=2.0, rate_max=3.1,
        rate_asof="정부 고시금리(분기별 변동) 참고값",
        max_loan_manwon=20000,
        max_deposit_manwon=BEOTIMOK_DEPOSIT_LIMIT_MANWON,
        target_grade="안전·주의",
        description=("무주택 청년(만 19~34세) 대상 정부 정책 전세자금대출. 시중 은행 자체상품보다 "
                     "낮은 금리로, 보증금 3억원(수도권 일반 기준) 이하 물건에 우선 매칭된다."),
        reference_url="https://kbthink.com/loan-guide/beotimok-youth.html",
    ),
    "KB_YOUTH_JEONSE": dict(
        product_code="KB_YOUTH_JEONSE",
        name="KB 청년 맞춤형 전세자금대출",
        provider="KB국민은행",
        product_type="은행자체대출(청년특화)",
        guarantee_agency="한국주택금융공사(HF)",
        rate_min=None, rate_max=None,
        rate_asof="신규취급액기준 COFIX + 가산금리 (변동금리, 실시간 금리는 은행 확인 필요)",
        max_loan_manwon=20000,
        max_deposit_manwon=None,
        target_grade="안전·주의 (버팀목 한도 초과)",
        description=("만 19~34세 무주택 청년 대상 KB 자체 전세자금대출. 임차보증금의 90% 이내, "
                     "최대 2억원. 한국주택금융공사(HF) 보증료 우대, 중도상환수수료 없음. "
                     "정부 정책자금 한도를 초과하는 보증금 물건에 매칭된다."),
        reference_url="https://kbthink.com/loan-guide/kb-youth-jeonse.html",
    ),
    "KB_STAR_HUG": dict(
        product_code="KB_STAR_HUG",
        name="KB스타 전세자금대출 (HUG 전세금안심대출보증)",
        provider="KB국민은행",
        product_type="은행자체대출 + 보증부(HUG)",
        guarantee_agency="주택도시보증공사(HUG)",
        rate_min=3.94, rate_max=5.34,
        rate_asof="2025-05 공시 기준 참고값 (변동)",
        max_loan_manwon=22200,
        max_deposit_manwon=None,
        target_grade="경고",
        description=("전세보증금반환보증이 함께 결합되는 HUG 연계 전세자금대출. 임차보증금의 "
                     "최대 80% 이내(채권보전조치 시 한도 확대 가능). 선순위채권비율이 높아 "
                     "반환보증 가입이 필요한 매물에 우선 매칭된다."),
        reference_url="https://www.khug.or.kr/khmb/m/hg/gg/relax/relaxsub3.jsp",
    ),
    "KB_STAR_HF": dict(
        product_code="KB_STAR_HF",
        name="KB스타 전세자금대출 (HF 한국주택금융공사)",
        provider="KB국민은행",
        product_type="은행자체대출 + 보증부(HF)",
        guarantee_agency="한국주택금융공사(HF)",
        rate_min=3.70, rate_max=None,
        rate_asof="2024년 공시 기준 최저 연 3.70%부터 (신용도별 상단은 은행 확인 필요)",
        max_loan_manwon=22200,
        max_deposit_manwon=None,
        target_grade="경고 (선순위채권비율 상대적으로 낮은 구간)",
        description=("한국주택금융공사(HF) 전세자금보증을 담보로 하는 은행자체 전세자금대출. "
                     "임차보증금의 최대 80% 이내, 최고 2억 2,200만원(채권보전조치 시 "
                     "4억 4,400만원). HF 보증료는 임차보증금의 0.02~0.4%로 HUG 결합형보다 "
                     "저렴해, 선순위채권 부담이 상대적으로 낮은 경고등급 매물에 매칭된다."),
        reference_url="https://kbthink.com/life/daily/hf-hug-sgi.html",
    ),
    "KB_STAR_SGI": dict(
        product_code="KB_STAR_SGI",
        name="KB스타 전세자금대출 (SGI 서울보증)",
        provider="KB국민은행",
        product_type="은행자체대출 + 보증부(SGI)",
        guarantee_agency="서울보증보험(SGI)",
        rate_min=3.70, rate_max=None,
        rate_asof="2025-05 공시 기준 최저 연 3.70%부터 (신용도·한도구간별 변동)",
        max_loan_manwon=50000,
        max_deposit_manwon=None,
        target_grade="전체 등급 (HUG·HF 한도를 초과하는 고액 전세)",
        description=("서울보증보험(SGI) 임차자금보험을 담보로 하는 은행자체 전세자금대출. "
                     "최소 500만원~최대 5억원(신용평점·임차보증금의 80%·부부합산 1주택 시 "
                     "최대 3억원 중 적은 금액). 아파트 등 보증한도 제한이 상대적으로 적어, "
                     "HUG·HF 보증형 상품의 한도(4억 4,400만원)를 초과하는 고액 전세 물건에 "
                     "매칭된다."),
        reference_url="https://kbthink.com/life/daily/hf-hug-sgi.html",
    ),
    "HUG_ANSIM_MANDATORY": dict(
        product_code="HUG_ANSIM_MANDATORY",
        name="전세보증금반환보증 가입 필수 안내 (대출 매칭 보류)",
        provider="주택도시보증공사(HUG)",
        product_type="보증보험 선가입 필수",
        guarantee_agency="HUG",
        rate_min=None, rate_max=None,
        rate_asof="대출 상품이 아닌 보증보험 안내 단계 (금리 해당 없음)",
        max_loan_manwon=None,
        max_deposit_manwon=None,
        target_grade="위험",
        description=("선순위채권(근저당+보증금)이 매매시세를 위협하는 고위험 매물입니다. "
                     "전세보증금반환보증 가입 가능 여부를 먼저 확인하고, 가입이 불가하다면 "
                     "계약 자체를 재검토해야 합니다. 이 단계에서는 대출 상품을 매칭하지 않습니다."),
        reference_url="https://www.khug.or.kr/hug/web/ig/dr/igdr000001.jsp",
    ),
}


@dataclass
class ProductMatch:
    product_code: str
    proposal_rate_adjust: float
    match_reason: str


def match_product(risk_score: int, risk_grade: str, jeonse_ratio: float,
                   senior_debt_ratio: float, deposit_manwon: int) -> ProductMatch:
    """위험등급(범주) + 선순위채권비율(연속값) + 보증금 규모를 결합한 다요소 매칭.

    동일한 위험등급 안에서도 실제 KB 상품 6종 중 비용·한도가 가장 맞는 상품을 골라,
    "등급→상품 1:1" 단순 매핑보다 세분화된(다양화된) 추천을 만든다.
    """
    if risk_grade == "위험":
        return ProductMatch(
            "HUG_ANSIM_MANDATORY", 0.0,
            f"선순위채권비율 {senior_debt_ratio:.0%}로 매매시세를 위협 → "
            f"보증보험 가입 여부 우선 확인 필요 (대출 매칭 보류)")

    # 위험 등급을 제외한 나머지는, 보증금이 HUG·HF 보증형 상품의 한도를 넘으면
    # 등급과 무관하게 한도 제한이 낮은 SGI 연계 상품만 실제로 커버 가능하다.
    if deposit_manwon > HF_HUG_MAX_COVERAGE_MANWON:
        return ProductMatch(
            "KB_STAR_SGI", 0.0,
            f"보증금 {deposit_manwon:,}만원이 HUG·HF 보증형 상품의 한도 "
            f"{HF_HUG_MAX_COVERAGE_MANWON:,}만원을 초과 → 한도가 넉넉한 SGI 연계 상품으로 매칭")

    if risk_grade == "경고":
        if senior_debt_ratio >= WARNING_HUG_COMBO_THRESHOLD:
            return ProductMatch(
                "KB_STAR_HUG", 0.0,
                f"선순위채권비율 {senior_debt_ratio:.0%} (≥{WARNING_HUG_COMBO_THRESHOLD:.0%}) → "
                f"HUG 전세보증금반환보증이 결합된 상품으로 매칭")
        return ProductMatch(
            "KB_STAR_HF", 0.0,
            f"선순위채권비율 {senior_debt_ratio:.0%} (<{WARNING_HUG_COMBO_THRESHOLD:.0%}) → "
            f"반환보증 결합 없이도 안전마진이 있어, 보증료가 더 저렴한 HF 보증 상품으로 매칭")

    # 안전 / 주의 등급: 보증금 규모로 정책자금 우선순위 판단
    if deposit_manwon <= BEOTIMOK_DEPOSIT_LIMIT_MANWON:
        return ProductMatch(
            "BEOTIMOK_YOUTH", 0.0,
            f"보증금 {deposit_manwon:,}만원 ≤ 정책자금 한도 "
            f"{BEOTIMOK_DEPOSIT_LIMIT_MANWON:,}만원 → 정부 최저금리 상품 우선 매칭")

    bonus = PLATFORM_SAFE_BONUS if risk_grade == "안전" else 0.0
    reason = f"보증금 {deposit_manwon:,}만원이 정책자금 한도를 초과해 KB 자체 청년전세대출로 매칭"
    if bonus:
        reason += f" · 안전매물 특별우대(제안) {bonus:+.1f}%p 추가 적용"
    return ProductMatch("KB_YOUTH_JEONSE", bonus, reason)
