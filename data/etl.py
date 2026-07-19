"""
etl.py — 공공데이터 → SQLite 적재 파이프라인 (프로토타입)

동작:
  1) db/schema.sql 로 스키마 생성
  2) 서울시 자치구 마스터 적재 (실제 좌표 + 기본 평균시세)
  3) 자치구 평균 시세 갱신:
       - SEOUL_API_KEY 환경변수가 있으면 서울 열린데이터광장 매매/전월세 실거래가 API를
         실제로 호출해(seoul_api.py) 자치구별 평균 매매가·전세가를 실데이터로 덮어쓰고,
         수집한 원본 거래를 transactions 테이블에 적재한다.
       - 키가 없거나 API 호출이 실패하면 → 기본 평균시세(FALLBACK 상수)를 그대로 사용한다.
     * 어떤 경우에도 네이버/직방 등 무단 크롤링은 하지 않는다 (컴플라이언스).
  4) 개별 매물(리스팅) 생성: 근저당·위반건축물 등 등기부/건축물대장 제휴 데이터는 아직
     연동되지 않았으므로 자치구 평균(실데이터 반영분 포함)을 기준으로 통계적으로 생성한다.
  5) risk_engine 으로 위험도 진단 + kb_products 로 KB국민은행 실제 상품 매칭 후 적재
  6) 금융상품 마스터(KB국민은행 실제 상품) 적재

사용:  python data/etl.py                        # FALLBACK 평균시세로 매물 300건 생성
       SEOUL_API_KEY=발급키 python data/etl.py     # 실제 서울시 실거래가로 자치구 평균시세 갱신
"""

import os
import sqlite3
import random
from pathlib import Path

from risk_engine import RiskInput, assess
from kb_products import PRODUCTS, match_product
import seoul_api

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "housing.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"

random.seed(42)  # 재현성

# 서울 자치구 대표 좌표 + FALLBACK 평균 시세(만원, 전용 60㎡ 환산 근사치)
# 좌표는 공개된 자치구 중심 좌표. 시세는 SEOUL_API_KEY 미설정 시에만 사용되는 근사값이며,
# 키가 있으면 seoul_api.fetch_district_averages() 의 실거래가 평균으로 대체된다.
DISTRICTS = [
    # code,   name,     lat,      lng,      avg_sale, avg_jeonse
    ("11680", "강남구", 37.5172, 127.0473, 145000, 78000),
    ("11650", "서초구", 37.4837, 127.0324, 138000, 74000),
    ("11710", "송파구", 37.5145, 127.1060, 110000, 62000),
    ("11440", "마포구", 37.5663, 126.9019,  92000, 55000),
    ("11170", "용산구", 37.5326, 126.9905, 118000, 60000),
    ("11215", "광진구", 37.5385, 127.0823,  82000, 50000),
    ("11290", "성북구", 37.5894, 127.0167,  68000, 43000),
    ("11305", "강북구", 37.6396, 127.0257,  52000, 35000),
    ("11500", "강서구", 37.5509, 126.8495,  70000, 44000),
    ("11470", "양천구", 37.5169, 126.8664,  85000, 50000),
    ("11530", "구로구", 37.4954, 126.8874,  62000, 40000),
    ("11545", "금천구", 37.4569, 126.8955,  58000, 38000),
    ("11620", "관악구", 37.4784, 126.9516,  60000, 41000),   # 대학가·청년 밀집
    ("11560", "영등포구", 37.5264, 126.8962, 78000, 48000),
    ("11350", "노원구", 37.6542, 127.0568,  55000, 37000),   # 대학가
    ("11380", "은평구", 37.6027, 126.9291,  62000, 41000),
    ("11110", "종로구", 37.5735, 126.9790,  88000, 52000),
    ("11140", "중구",   37.5636, 126.9976,  90000, 53000),
    ("11230", "동대문구", 37.5744, 127.0396, 66000, 44000),  # 대학가
    ("11260", "중랑구", 37.6063, 127.0925,  52000, 36000),
    ("11320", "도봉구", 37.6688, 127.0471,  50000, 34000),
    ("11410", "서대문구", 37.5791, 126.9368, 74000, 47000),  # 대학가(신촌)
    ("11590", "동작구", 37.5124, 126.9393,  76000, 47000),
    ("11740", "강동구", 37.5301, 127.1238,  84000, 51000),
    ("11200", "성동구", 37.5634, 127.0369,  90000, 54000),
]

BUILDING_TYPES = ["오피스텔", "다세대", "빌라", "도시형생활주택", "아파트"]

# 유형별 시세 배율. 서울시 열린데이터광장 avg_sale_price는 자치구 평균(아파트 실거래
# 위주)이므로, 소형 주거유형(오피스텔·다세대·빌라 등 청년 1인가구 주 거주형태)은
# 같은 자치구 안에서도 아파트보다 훨씬 낮게 거래되는 실제 시장 구조를 반영해 축소한다.
# 이렇게 해야 보증금 규모가 골고루 퍼져 KB 상품 매칭(버팀목/HF/HUG/SGI)도 자치구
# 평균값 하나에 쏠리지 않고 다양화된다.
BUILDING_TYPE_PRICE_FACTOR = {
    "오피스텔": 0.55,
    "다세대": 0.42,
    "빌라": 0.38,
    "도시형생활주택": 0.48,
    "아파트": 1.0,
}


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_districts(conn):
    conn.executemany(
        "INSERT OR REPLACE INTO districts VALUES (?,?,?,?,?,?)", DISTRICTS
    )


def load_finance_products(conn):
    cols = ["product_code", "name", "provider", "product_type", "guarantee_agency",
            "rate_min", "rate_max", "rate_asof", "max_loan_manwon", "max_deposit_manwon",
            "target_grade", "description", "reference_url"]
    rows = [tuple(p[c] for c in cols) for p in PRODUCTS.values()]
    placeholders = ",".join("?" * len(cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO finance_products ({','.join(cols)}) VALUES ({placeholders})",
        rows,
    )


def apply_real_seoul_data(conn):
    """SEOUL_API_KEY 가 있으면 실제 자치구 평균 시세로 districts 테이블을 갱신하고
    수집한 원본 거래를 transactions 테이블에 적재한다. 실패하면 조용히 FALLBACK 유지."""
    key = os.environ.get("SEOUL_API_KEY")
    if not key:
        print("[etl] SEOUL_API_KEY 미설정 → FALLBACK 평균시세 사용")
        return False

    print("[etl] SEOUL_API_KEY 감지 → 서울 열린데이터광장 실거래가 API 호출 시도")
    try:
        averages, transactions = seoul_api.fetch_district_averages(key)
    except Exception as e:
        print(f"[etl] 실거래가 API 호출 실패 → FALLBACK 평균시세 유지: {e}")
        return False

    updated = 0
    for code, agg in averages.items():
        row = conn.execute(
            "SELECT avg_sale_price, avg_jeonse FROM districts WHERE district_code=?",
            (code,)).fetchone()
        if row is None:
            continue  # 우리 DISTRICTS 마스터에 없는 코드는 스킵 (서울 외 지역 등)
        new_sale = agg["avg_sale"] or row[0]
        new_jeonse = agg["avg_jeonse"] or row[1]
        conn.execute(
            "UPDATE districts SET avg_sale_price=?, avg_jeonse=? WHERE district_code=?",
            (new_sale, new_jeonse, code))
        updated += 1

    for tx in transactions:
        if not conn.execute("SELECT 1 FROM districts WHERE district_code=?",
                            (tx["district_code"],)).fetchone():
            continue
        conn.execute(
            """INSERT INTO transactions
               (district_code, deal_type, price, monthly_rent, area_m2, build_year,
                deal_date, raw_ref, source)
               VALUES (?,?,?,?,?,?,?,?, 'seoul_open_data')""",
            (tx["district_code"], tx["deal_type"], tx["price"], tx["monthly_rent"],
             tx["area_m2"], tx["build_year"], tx["deal_date"], tx["raw_ref"]))

    print(f"[etl] 실거래가 반영 완료: 자치구 {updated}개 평균시세 갱신, "
          f"원본 거래 {len(transactions)}건 적재")
    return True


def generate_properties(conn, per_district=12):
    """자치구 평균 시세(실데이터 반영 가능) 기준으로 매물을 생성하고 위험도·KB상품을 매칭."""
    rows = conn.execute("SELECT district_code, name, lat, lng, avg_sale_price, "
                        "avg_jeonse FROM districts").fetchall()
    prop_count = 0
    for code, name, lat, lng, avg_sale, avg_jeonse in rows:
        for i in range(per_district):
            # 좌표: 구 중심 ± 약 0.02도 산포
            plat = round(lat + random.uniform(-0.018, 0.018), 6)
            plng = round(lng + random.uniform(-0.022, 0.022), 6)
            btype = random.choices(BUILDING_TYPES, weights=[30, 25, 20, 15, 10])[0]
            area = round(random.uniform(24, 59), 1)
            build_year = random.randint(1998, 2022)

            # 시세: 구 평균(아파트 기준) × 유형별 배율 × ±30% 산포
            sale_price = int(avg_sale * BUILDING_TYPE_PRICE_FACTOR[btype] * random.uniform(0.7, 1.3))
            # 전세가율: 대체로 55~95%, 일부 극단(깡통) 케이스
            jeonse_ratio = random.choices(
                [random.uniform(0.5, 0.7),
                 random.uniform(0.7, 0.85),
                 random.uniform(0.85, 1.05)],
                weights=[45, 35, 20])[0]
            deposit = int(sale_price * jeonse_ratio)
            # 근저당: 0 ~ 시세의 60% (등기부 제휴 전이므로 통계적 추정)
            mortgage = int(sale_price * random.choices(
                [0, random.uniform(0.05, 0.25), random.uniform(0.25, 0.6)],
                weights=[35, 40, 25])[0])
            is_illegal = 1 if random.random() < 0.08 else 0

            cur = conn.execute(
                """INSERT INTO properties
                   (district_code, address, lat, lng, building_type, area_m2,
                    build_year, sale_price, deposit, mortgage_amount, is_illegal, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'partner_agency')""",
                (code, f"{name} {btype} {i+1}호", plat, plng, btype, area,
                 build_year, sale_price, deposit, mortgage, is_illegal))
            pid = cur.lastrowid

            r = assess(RiskInput(sale_price, deposit, mortgage, bool(is_illegal)))
            m = match_product(r.risk_score, r.risk_grade, r.jeonse_ratio,
                               r.senior_debt_ratio, deposit)
            conn.execute(
                """INSERT INTO risk_assessments
                   (property_id, jeonse_ratio, senior_debt_ratio, risk_score,
                    risk_grade, recommended_product, proposal_rate_adjust, match_reason)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (pid, r.jeonse_ratio, r.senior_debt_ratio, r.risk_score,
                 r.risk_grade, m.product_code, m.proposal_rate_adjust, m.match_reason))
            prop_count += 1
    return prop_count


def main():
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        load_districts(conn)
        load_finance_products(conn)
        used_real_data = apply_real_seoul_data(conn)
        n = generate_properties(conn)
        conn.commit()
        print(f"[etl] 완료: {DB_PATH}")
        print(f"[etl] 자치구 {len(DISTRICTS)}개 (평균시세 소스: "
              f"{'서울 열린데이터광장 실거래가' if used_real_data else 'FALLBACK 근사값'}), "
              f"금융상품 {len(PRODUCTS)}개, 매물 {n}건 적재")
        dist = conn.execute(
            "SELECT risk_grade, COUNT(*) FROM v_property_latest_risk "
            "GROUP BY risk_grade").fetchall()
        print("[etl] 위험등급 분포:", dict(dist))
        prod = conn.execute(
            "SELECT recommended_product, COUNT(*) FROM v_property_latest_risk "
            "GROUP BY recommended_product").fetchall()
        print("[etl] 추천 KB상품 분포:", dict(prod))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
