"""
seoul_api.py — 서울 열린데이터광장 실거래가 API 연동 (SEOUL_API_KEY 필요)

사용하는 실제 공공데이터셋 (서울 열린데이터광장, data.seoul.go.kr):
  - OA-21275  서울시 부동산 실거래가 정보   (서비스명: tbLnOpendataRtms)
  - OA-21276  서울시 부동산 전월세가 정보   (서비스명: tbLnOpendataRentV)

요청 형식(서울 열린데이터광장 공통 규격):
  http://openapi.seoul.go.kr:8088/{인증키}/json/{서비스명}/{시작인덱스}/{끝인덱스}/

응답 형식(서울 열린데이터광장 공통 규격):
  { "<서비스명>": { "list_total_count": N,
                    "RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다"},
                    "row": [ {...}, ... ] } }

⚠️ 필드명 주의사항
  두 데이터셋의 정확한 컬럼명은 API 문서(위 데이터셋 페이지의 '샘플/파일' 또는 상세설명)를
  통해 확정해야 한다. 이 모듈은 공개 자료에서 통상적으로 쓰이는 후보 컬럼명들을
  FIELD_CANDIDATES 로 나열해 두고, 실제 응답에서 후보 중 존재하는 첫 컬럼을 채택하는
  방식으로 동작한다. 후보가 하나도 매칭되지 않으면 예외 대신 원본 키 목록을 로그로 남기고
  해당 레코드를 건너뛴다 — 존재하지 않는 필드명을 임의로 지어내 잘못된 값을 저장하는 것을
  방지하기 위함이다. 실제 키와 다르면 FIELD_CANDIDATES 에 실제 키를 추가해서 재실행하면 된다.
"""

import json
import urllib.request
import urllib.error
from collections import defaultdict

BASE_URL = "http://openapi.seoul.go.kr:8088"
PAGE_SIZE = 1000
TIMEOUT = 10

SERVICE_RTMS = "tbLnOpendataRtms"      # 매매 실거래가
SERVICE_RENT = "tbLnOpendataRentV"     # 전월세가

# 논리 필드 → 실제 응답에서 존재할 수 있는 후보 컬럼명(우선순위 순)
FIELD_CANDIDATES = {
    "district_code": ["SGG_CD", "CGG_CD", "GU_CD"],
    "district_name": ["SGG_NM", "CGG_NM", "GU_NM"],
    "deal_amount":   ["THING_AMT", "OBJ_AMT", "MMB_AMT"],       # 매매 물건금액(만원)
    "deposit":       ["GRFE", "DEPOSIT"],                        # 전월세 보증금(만원)
    "monthly_rent":  ["RTFE", "MONTHLY_RENT"],                   # 월세(만원)
    "rent_type":     ["RENT_SE", "RENT_GBN"],                    # 전세/월세 구분
    "area":          ["ARCH_AREA", "RENT_AREA"],                 # 면적(㎡)
    "build_year":    ["ARCH_YR", "BLDG_YY"],
    "deal_date":     ["CTRT_DAY", "DCLR_YMD"],
    "dong_name":     ["STDG_NM", "BJDONG_NM"],
    "bldg_name":     ["BLDG_NM"],
}


def _pick(row: dict, logical_field: str):
    for key in FIELD_CANDIDATES[logical_field]:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _fetch_page(api_key, service, start, end):
    url = f"{BASE_URL}/{api_key}/json/{service}/{start}/{end}/"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    body = payload.get(service)
    if not body:
        # 인증 오류 등은 RESULT 최상위에 바로 오는 경우가 있음
        raise RuntimeError(f"[seoul_api] 예상치 못한 응답 형식: {list(payload.keys())}")
    result = body.get("RESULT", {})
    code = result.get("CODE", "")
    if code and not code.startswith("INFO-0"):
        raise RuntimeError(f"[seoul_api] API 오류 {code}: {result.get('MESSAGE')}")
    return body.get("row", []), body.get("list_total_count", 0)


def fetch_rows(api_key, service, max_rows=4000):
    """페이지네이션으로 최대 max_rows 건 수집. 실패 시 예외를 올려 호출부에서 FALLBACK 처리."""
    rows, start, total = [], 1, None
    while len(rows) < max_rows:
        end = min(start + PAGE_SIZE - 1, start + max_rows - len(rows) - 1)
        page, total = _fetch_page(api_key, service, start, end)
        if not page:
            break
        rows.extend(page)
        if total and len(rows) >= total:
            break
        start += PAGE_SIZE
    return rows


def fetch_district_averages(api_key, max_rows_each=4000, max_tx_store=800):
    """
    매매(RTMS) + 전월세(RentV) 데이터를 수집해 자치구별 평균 매매가·평균 전세가를 산출.
    개별 매물의 근저당·위반건축물 등은 이 API 범위 밖(등기부/건축물대장 제휴 필요)이므로
    다루지 않는다 — 자치구 단위 시세 평균만 실데이터로 대체한다.

    반환: (averages, transactions)
      averages    = { district_code: {"name":.., "avg_sale":.., "n_sale":..,
                                       "avg_jeonse":.., "n_jeonse":..} }
      transactions = [ {district_code, deal_type, price, monthly_rent, area_m2,
                         build_year, deal_date, raw_ref}, ... ]  (DB 원본 적재용, 최대 max_tx_store건)
    """
    sale_bucket = defaultdict(list)
    jeonse_bucket = defaultdict(list)
    names = {}
    unmatched_sample = None
    transactions = []

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # 1) 매매 실거래가
    try:
        rtms_rows = fetch_rows(api_key, SERVICE_RTMS, max_rows_each)
    except Exception as e:
        print(f"[seoul_api] 매매 실거래가 수집 실패: {e}")
        rtms_rows = []
    for row in rtms_rows:
        code, name = _pick(row, "district_code"), _pick(row, "district_name")
        amt = _num(_pick(row, "deal_amount"))
        if code and amt:
            sale_bucket[code].append(amt)
            names[code] = name or names.get(code)
            if len(transactions) < max_tx_store:
                transactions.append({
                    "district_code": code, "deal_type": "매매", "price": int(amt),
                    "monthly_rent": 0, "area_m2": _num(_pick(row, "area")),
                    "build_year": _num(_pick(row, "build_year")),
                    "deal_date": _pick(row, "deal_date"),
                    "raw_ref": _pick(row, "dong_name") or _pick(row, "bldg_name"),
                })
        elif unmatched_sample is None:
            unmatched_sample = list(row.keys())

    # 2) 전월세가 (전세/월세 모두 적재, 전세가율 산출에는 전세만 사용)
    try:
        rent_rows = fetch_rows(api_key, SERVICE_RENT, max_rows_each)
    except Exception as e:
        print(f"[seoul_api] 전월세가 수집 실패: {e}")
        rent_rows = []
    for row in rent_rows:
        code, name = _pick(row, "district_code"), _pick(row, "district_name")
        deposit = _num(_pick(row, "deposit"))
        monthly = _num(_pick(row, "monthly_rent")) or 0
        rent_type = (_pick(row, "rent_type") or "").strip()
        is_jeonse = (not rent_type) or ("전세" in rent_type)
        if code and deposit:
            names[code] = name or names.get(code)
            if is_jeonse:
                jeonse_bucket[code].append(deposit)
            if len(transactions) < max_tx_store:
                transactions.append({
                    "district_code": code,
                    "deal_type": "전세" if is_jeonse else "월세",
                    "price": int(deposit), "monthly_rent": int(monthly),
                    "area_m2": _num(_pick(row, "area")),
                    "build_year": _num(_pick(row, "build_year")),
                    "deal_date": _pick(row, "deal_date"),
                    "raw_ref": _pick(row, "dong_name") or _pick(row, "bldg_name"),
                })
        elif unmatched_sample is None:
            unmatched_sample = list(row.keys())

    if not sale_bucket and not jeonse_bucket:
        sample_msg = f" (샘플 응답 키: {unmatched_sample})" if unmatched_sample else ""
        raise RuntimeError(
            "[seoul_api] 응답에서 컬럼을 하나도 매칭하지 못했습니다. "
            "FIELD_CANDIDATES 를 실제 API 응답 키로 갱신하세요." + sample_msg)

    averages = {}
    codes = set(sale_bucket) | set(jeonse_bucket)
    for code in codes:
        sales = sale_bucket.get(code, [])
        jeonses = jeonse_bucket.get(code, [])
        averages[code] = {
            "name": names.get(code),
            "avg_sale": round(sum(sales) / len(sales)) if sales else None,
            "n_sale": len(sales),
            "avg_jeonse": round(sum(jeonses) / len(jeonses)) if jeonses else None,
            "n_jeonse": len(jeonses),
        }
    return averages, transactions


if __name__ == "__main__":
    import os
    key = os.environ.get("SEOUL_API_KEY")
    if not key:
        print("SEOUL_API_KEY 환경변수를 설정하세요.")
    else:
        averages, txs = fetch_district_averages(key, max_rows_each=500)
        for code, v in sorted(averages.items()):
            print(code, v)
        print(f"수집된 원본 거래 {len(txs)}건")
