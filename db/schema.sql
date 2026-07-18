-- =====================================================================
-- 청년·대학생 주택 안전진단 & 맞춤형 금융 연계 플랫폼
-- SQLite 스키마 (프로토타입)
--
-- 설계 원칙
--   1) 모든 원천 데이터는 "출처(source)"와 "수집일시"를 기록해 컴플라이언스 추적성 확보
--   2) 무단 크롤링 데이터는 저장하지 않음 → source 컬럼은 공공/제휴 채널만 허용
--   3) 위험도(risk_score)는 원천 데이터로부터 재계산 가능하도록 입력값을 함께 저장
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. 행정구역(자치구) 마스터
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS districts (
    district_code   TEXT PRIMARY KEY,          -- 법정동/자치구 코드
    name            TEXT NOT NULL,             -- 예: 관악구
    lat             REAL NOT NULL,             -- 자치구 대표 위도
    lng             REAL NOT NULL,             -- 자치구 대표 경도
    avg_sale_price  INTEGER,                   -- 지역 평균 매매가 (만원)
    avg_jeonse      INTEGER                     -- 지역 평균 전세가 (만원)
);

-- ---------------------------------------------------------------------
-- 2. 실거래가 원천 데이터 (서울시 열린데이터광장 / 국토부 실거래가 API)
--    * source 는 공공데이터 채널만 허용
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    tx_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    district_code   TEXT NOT NULL REFERENCES districts(district_code),
    deal_type       TEXT NOT NULL CHECK (deal_type IN ('매매','전세','월세')),
    price           INTEGER NOT NULL,          -- 거래 금액 (만원). 전세=보증금
    monthly_rent    INTEGER DEFAULT 0,         -- 월세 (만원)
    area_m2         REAL,                      -- 전용면적
    build_year      INTEGER,
    deal_date       TEXT,                      -- YYYY-MM-DD
    source          TEXT NOT NULL DEFAULT 'seoul_open_data'
                        CHECK (source IN ('seoul_open_data','molit_api','partner_agency')),
    collected_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- 3. 매물(진단 대상) — 고객이 조회/계약 검토하는 개별 물건
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS properties (
    property_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    district_code   TEXT NOT NULL REFERENCES districts(district_code),
    address         TEXT NOT NULL,
    lat             REAL NOT NULL,
    lng             REAL NOT NULL,
    building_type   TEXT,                      -- 아파트/오피스텔/다세대 등
    area_m2         REAL,
    build_year      INTEGER,

    -- 진단 입력값 (모두 공공/제휴 데이터에서 산출)
    sale_price      INTEGER NOT NULL,          -- 추정 매매 시세 (만원)
    deposit         INTEGER NOT NULL,          -- 전세 보증금 (만원)
    mortgage_amount INTEGER NOT NULL DEFAULT 0,-- 근저당 설정액 (만원, 등기부)
    is_illegal      INTEGER NOT NULL DEFAULT 0,-- 위반건축물 여부(건축물대장) 0/1

    source          TEXT NOT NULL DEFAULT 'partner_agency'
                        CHECK (source IN ('seoul_open_data','molit_api','iros_registry','partner_agency')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- 4. 위험도 진단 결과 (properties 1:N — 재진단 이력 관리)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_assessments (
    assessment_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id         INTEGER NOT NULL REFERENCES properties(property_id),
    jeonse_ratio        REAL,                  -- 전세가율 = 보증금/매매가
    senior_debt_ratio   REAL,                  -- 선순위채권비율 = (근저당+보증금)/매매가
    risk_score          INTEGER NOT NULL,      -- 0~100 (높을수록 위험)
    risk_grade          TEXT NOT NULL,         -- 안전/주의/경고/위험
    recommended_product TEXT,                  -- 추천 금융상품 코드
    assessed_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- 5. 금융상품 마스터 (은행 연계)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS finance_products (
    product_code    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    product_type    TEXT NOT NULL,             -- 보증보험 / 전월세대출 / 우대금리대출
    target_grade    TEXT NOT NULL,             -- 매칭 대상 위험등급
    base_rate       REAL,                      -- 기준 금리(%)
    rate_adjust     REAL DEFAULT 0,            -- 차등(우대/가산) 금리(%)
    description     TEXT
);

-- ---------------------------------------------------------------------
-- 조회 편의를 위한 뷰: 매물 + 최신 진단 결과
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_property_latest_risk AS
SELECT p.property_id, p.address, p.lat, p.lng, p.building_type,
       p.sale_price, p.deposit, p.mortgage_amount, p.is_illegal,
       d.name AS district_name,
       r.jeonse_ratio, r.senior_debt_ratio, r.risk_score, r.risk_grade,
       r.recommended_product
FROM properties p
JOIN districts d ON d.district_code = p.district_code
JOIN risk_assessments r ON r.property_id = p.property_id
WHERE r.assessment_id = (
    SELECT MAX(assessment_id) FROM risk_assessments r2 WHERE r2.property_id = p.property_id
);

CREATE INDEX IF NOT EXISTS idx_tx_district ON transactions(district_code);
CREATE INDEX IF NOT EXISTS idx_prop_district ON properties(district_code);
CREATE INDEX IF NOT EXISTS idx_risk_property ON risk_assessments(property_id);
