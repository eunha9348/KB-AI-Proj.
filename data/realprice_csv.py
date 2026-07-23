"""
realprice_csv.py — 국토교통부 실거래가 공개시스템(rt.molit.go.kr) CSV 로더

왜 CSV 인가
  rt.molit.go.kr 에서 건물유형·전월세/매매·지역·기간을 골라 **로그인·인증키 없이** Excel/CSV
  를 바로 내려받을 수 있다. 이 파일을 data/ 에 넣으면 API 키 없이도 '실제 단지명·보증금·
  전용면적·건축년도·계약일'을 가진 **진짜 매물**로 서비스를 구성할 수 있다.

포맷(공개시스템 다운로드 기준)
  - 인코딩: CP949(euc-kr). (UTF-8 로 저장된 경우도 자동 감지)
  - 상단에 검색조건 프리앰블 몇 줄 → 이후 헤더 행 → 데이터.
  - 아파트 전월세 헤더(예): 시군구, 번지, 단지명, 전월세구분, 전용면적(㎡), 계약년월,
      계약일, 보증금(만원), 월세금(만원), 층, 건축년도, 도로명 …
  - 아파트 매매 헤더(예): 시군구, 단지명, 전용면적(㎡), 계약년월, 계약일,
      거래금액(만원), 층, 건축년도, 도로명 …

⚠️ 허구 금지: 컬럼명이 조금 달라도 후보 토큰 '포함' 매칭으로 찾고, 핵심 컬럼을 못 찾으면
   그 행/파일을 건너뛴다(값을 지어내지 않음). 금액의 콤마·공백은 정규화한다.
"""

import csv
import io
import re
from pathlib import Path

# 논리 필드 → 헤더에 '포함'되어야 하는 핵심 토큰. 위에서부터 먼저 컬럼을 선점한다
# (예: '전월세구분'이 '월세'를 포함하므로 rent_type 을 monthly 보다 먼저 매칭해야 한다).
HEADER_TOKENS = {
    "sigungu":    ["시군구"],
    "complex":    ["단지명", "건물명"],
    "rent_type":  ["전월세구분"],
    "area":       ["전용면적"],
    "amount":     ["거래금액"],
    "deposit":    ["보증금"],          # '종전계약 보증금'보다 먼저 나오는 '보증금(만원)' 선점
    "monthly":    ["월세금", "월세"],
    "ym":         ["계약년월"],
    "day":        ["계약일"],
    "build_year": ["건축년도", "건축연도"],
    "floor":      ["층"],
    "road":       ["도로명"],
}


def _decode(raw: bytes) -> str:
    for enc in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _num(v):
    if v is None:
        return None
    s = re.sub(r"[,\s]", "", str(v))
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_header(rows):
    """'시군구' 와 '단지명'(또는 거래금액/보증금) 이 함께 있는 첫 행을 헤더로 본다."""
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        joined = " ".join(cells)
        if any("시군구" in c for c in cells) and (
                "단지명" in joined or "거래금액" in joined or "보증금" in joined):
            return i
    return None


def _colmap(header):
    hs = [h.strip() for h in header]
    m = {}
    used = set()   # 한 컬럼은 한 필드에만 배정(먼저 선언된 필드가 선점)
    for field, tokens in HEADER_TOKENS.items():
        for idx, h in enumerate(hs):
            if idx in used:
                continue
            if any(tok in h for tok in tokens):
                m[field] = idx
                used.add(idx)
                break
    return m


_GU = re.compile(r"([가-힣]+(?:구|군|시))")


def _district_from_sigungu(s):
    """'서울특별시 강남구 역삼동' → '강남구'. 서울 자치구만 대상."""
    if not s:
        return None
    toks = _GU.findall(s)
    for t in toks:
        if t.endswith("구"):
            return t
    # '구'가 없으면(시·군) 마지막 토큰
    return toks[-1] if toks else None


def load_transactions(csv_path, allowed_districts=None):
    """CSV → 실거래 리스트. 각 원소:
       {district_name, complex, deal_type('매매'/'전세'/'월세'), price(만원),
        monthly, area_m2, build_year, deal_date, floor, road}
    allowed_districts: 이 자치구명 집합에 속한 거래만 반환(None=전체).
    """
    raw = Path(csv_path).read_bytes()
    text = _decode(raw)
    rows = list(csv.reader(io.StringIO(text)))
    hidx = _find_header(rows)
    if hidx is None:
        return []
    cmap = _colmap(rows[hidx])
    out = []

    def cell(row, field):
        i = cmap.get(field)
        return row[i].strip() if (i is not None and i < len(row)) else None

    for row in rows[hidx + 1:]:
        if not row or all(not c.strip() for c in row):
            continue
        dist = _district_from_sigungu(cell(row, "sigungu"))
        if not dist:
            continue
        if allowed_districts is not None and dist not in allowed_districts:
            continue
        area = _num(cell(row, "area"))
        by = _num(cell(row, "build_year"))
        ym = cell(row, "ym")
        day = cell(row, "day")
        date = None
        if ym and len(re.sub(r"\D", "", ym)) >= 6:
            ymd = re.sub(r"\D", "", ym)[:6]
            d2 = re.sub(r"\D", "", day or "")[:2].rjust(2, "0") if day else "01"
            date = f"{ymd[:4]}-{ymd[4:6]}-{d2}"
        comp = cell(row, "complex")
        floor = cell(row, "floor")
        road = cell(row, "road")

        amount = _num(cell(row, "amount"))       # 매매
        deposit = _num(cell(row, "deposit"))     # 전월세
        if amount:
            out.append({"district_name": dist, "complex": comp, "deal_type": "매매",
                        "price": int(amount), "monthly": 0, "area_m2": area,
                        "build_year": int(by) if by else None, "deal_date": date,
                        "floor": floor, "road": road})
        elif deposit:
            rt = cell(row, "rent_type") or ""
            monthly = _num(cell(row, "monthly")) or 0
            dtype = "전세" if (monthly == 0 and "월세" not in rt) else "월세"
            out.append({"district_name": dist, "complex": comp, "deal_type": dtype,
                        "price": int(deposit), "monthly": int(monthly), "area_m2": area,
                        "build_year": int(by) if by else None, "deal_date": date,
                        "floor": floor, "road": road})
    return out


def load_dir(data_dir, allowed_districts=None):
    """data_dir 안의 realprice*.csv 를 모두 읽어 합친다. 반환: (txs, files)."""
    data_dir = Path(data_dir)
    files = sorted(data_dir.glob("realprice*.csv"))
    txs = []
    for f in files:
        try:
            txs.extend(load_transactions(f, allowed_districts))
        except Exception as e:
            print(f"[realprice_csv] {f.name} 읽기 실패: {e}")
    return txs, [f.name for f in files]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        txs = load_transactions(sys.argv[1])
        print(f"거래 {len(txs)}건")
        for t in txs[:5]:
            print(t)
