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

⚠️ ERROR-500 대응 (실측 근거)
  서울 열린데이터광장 매매(tbLnOpendataRtms) 엔드포인트는 간헐적으로 `ERROR-500
  (서버 오류입니다)` 를 반환한다(서버 측 일시 오류 — 클라이언트가 근본 해결은 불가).
  이를 완화하기 위해 (1) 한 번에 요청하는 페이지 크기를 줄이고 (2) ERROR-500/타임아웃/
  네트워크 오류에 대해 지수 백오프 재시도를 적용한다. 그래도 실패하면 해당 지표만
  FALLBACK 을 유지하고, "이 지표는 실데이터가 아니다" 라는 사실을 호출부가 알 수 있도록
  provenance(출처 성공 여부)를 함께 반환한다 — 실패한 지표를 실데이터인 척 표기하지 않기
  위함이다.

⚠️ 필드명 주의사항
  두 데이터셋의 정확한 컬럼명은 API 문서를 통해 확정해야 한다. 이 모듈은 공개 자료에서
  통상적으로 쓰이는 후보 컬럼명들을 FIELD_CANDIDATES 로 나열하고, 실제 응답에서 후보 중
  존재하는 첫 컬럼을 채택한다. 후보가 하나도 매칭되지 않으면 예외 대신 원본 키 목록을
  로그로 남기고 건너뛴다 — 존재하지 않는 필드명을 지어내 잘못된 값을 저장하지 않기 위함.
"""

import json
import time
import urllib.request
import urllib.error
from collections import defaultdict

BASE_URL = "http://openapi.seoul.go.kr:8088"
# ERROR-500 완화: 큰 인덱스 범위 요청이 서버 500 을 유발하는 경향이 있어 페이지를 작게 잡는다.
PAGE_SIZE = 300
TIMEOUT = 25            # 해외 리전(GitHub Actions)에서 국내 정부망 호출 시 지연 대비
MAX_RETRIES = 4         # ERROR-500/타임아웃 재시도 횟수
BACKOFF_BASE = 1.5      # 지수 백오프 기준(초): 1.5, 3.0, 6.0 ...

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


class SeoulApiError(RuntimeError):
    """재시도해도 복구되지 않은 API 오류(호출부에서 FALLBACK 유지 신호로 사용)."""


def _pick(row: dict, logical_field: str):
    for key in FIELD_CANDIDATES[logical_field]:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


class _Transient(Exception):
    """일시 오류(재시도 가능): ERROR-500(서버 오류)/타임아웃/네트워크 등."""


def _raise_for_result(code, msg, raw=None):
    """API RESULT 코드가 정상(INFO-0*)이 아니면 일시/영구 오류로 분기해 예외를 던진다."""
    if not code or code.startswith("INFO-0"):
        return
    # ERROR-500(서버 오류) 등 5xx 계열은 일시 오류 → 재시도 대상
    if code.startswith("ERROR-5") or "서버 오류" in (msg or ""):
        raise _Transient(f"{code}: {msg}")
    raise SeoulApiError(f"{code}: {msg or raw}")


def _request_once(api_key, service, start, end):
    url = f"{BASE_URL}/{api_key}/json/{service}/{start}/{end}/"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    body = payload.get(service)
    if not body:
        # 인증키 미승인/활용신청 미완료/서버 오류 등은 RESULT 가 최상위로 바로 온다.
        top = payload.get("RESULT") or {}
        _raise_for_result(top.get("CODE", ""), top.get("MESSAGE", ""), raw=payload)
        # RESULT 조차 없으면 예상치 못한 형식(영구 오류)
        raise SeoulApiError(f"예상치 못한 응답 형식: {list(payload.keys())}")
    _raise_for_result(body.get("RESULT", {}).get("CODE", ""),
                      body.get("RESULT", {}).get("MESSAGE", ""))
    return body.get("row", []), body.get("list_total_count", 0)


def _fetch_page(api_key, service, start, end):
    """단일 페이지 요청 + 일시 오류(ERROR-500/타임아웃/네트워크) 지수 백오프 재시도."""
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            return _request_once(api_key, service, start, end)
        except (_Transient, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"[seoul_api] {service} {start}-{end} 일시 오류({e}) → "
                      f"{wait:.1f}s 후 재시도 ({attempt + 1}/{MAX_RETRIES - 1})")
                time.sleep(wait)
    raise SeoulApiError(f"{service} 재시도 소진: {last}")


def fetch_rows(api_key, service, max_rows=3000):
    """페이지네이션으로 최대 max_rows 건 수집. 최종 실패 시 SeoulApiError 를 올린다."""
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


def fetch_district_averages(api_key, max_rows_each=3000, max_tx_store=800):
    """
    매매(RTMS) + 전월세(RentV) 데이터를 수집해 자치구별 평균 매매가·평균 전세가를 산출.

    반환: (averages, transactions, provenance)
      averages    = { district_code: {"name":.., "avg_sale":.., "n_sale":..,
                                       "avg_jeonse":.., "n_jeonse":..,
                                       "jeonse_std":.. } }
      transactions = [ {district_code, deal_type, price, monthly_rent, area_m2,
                         build_year, deal_date, raw_ref}, ... ]  (최대 max_tx_store건)
      provenance  = {"sale_from_api": bool, "jeonse_from_api": bool,
                     "sale_error": str|None, "jeonse_error": str|None,
                     "n_sale_rows": int, "n_jeonse_rows": int}
        → 호출부가 지표별로 실데이터 여부를 정직하게 표기할 수 있게 한다.
    """
    sale_bucket = defaultdict(list)
    jeonse_bucket = defaultdict(list)
    names = {}
    unmatched_sample = None
    transactions = []
    prov = {"sale_from_api": False, "jeonse_from_api": False,
            "sale_error": None, "jeonse_error": None,
            "n_sale_rows": 0, "n_jeonse_rows": 0}

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
        prov["sale_error"] = str(e)
    prov["n_sale_rows"] = len(rtms_rows)
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
        prov["jeonse_error"] = str(e)
    prov["n_jeonse_rows"] = len(rent_rows)
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

    prov["sale_from_api"] = bool(sale_bucket)
    prov["jeonse_from_api"] = bool(jeonse_bucket)

    if not sale_bucket and not jeonse_bucket:
        sample_msg = f" (샘플 응답 키: {unmatched_sample})" if unmatched_sample else ""
        raise SeoulApiError(
            "응답에서 컬럼을 하나도 매칭하지 못했습니다. "
            "FIELD_CANDIDATES 를 실제 API 응답 키로 갱신하세요." + sample_msg)

    def _std(xs):
        if len(xs) < 2:
            return None
        m = sum(xs) / len(xs)
        return round((sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5, 1)

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
            # 전세가 변동성(표준편차): 지역 시장 불안정성 지표로 위험도 산출에 사용
            "jeonse_std": _std(jeonses),
        }
    return averages, transactions, prov


if __name__ == "__main__":
    import os
    key = os.environ.get("SEOUL_API_KEY")
    if not key:
        print("SEOUL_API_KEY 환경변수를 설정하세요.")
    else:
        averages, txs, prov = fetch_district_averages(key, max_rows_each=600)
        print("provenance:", prov)
        for code, v in sorted(averages.items()):
            print(code, v)
        print(f"수집된 원본 거래 {len(txs)}건")
