# 사용 설명서 (USAGE)

## 1. 30초 안에 프로토타입 보기
`frontend/standalone.html` 을 더블클릭 → 브라우저에서 지도/도표가 바로 뜹니다.
설치·서버·인터넷 모두 필요 없습니다. (데이터가 파일에 내장되어 있음)

## 2. 데이터부터 다시 만들기
```bash
python data/etl.py               # ① DB 생성 (db/housing.db)
python data/export_json.py       # ② DB → frontend/data.json
python data/build_standalone.py  # ③ standalone.html 재빌드
```

## 3. 라이브(백엔드) 모드
```bash
python backend/app.py            # http://localhost:8000  (실지도 + API)
```
Flask가 설치돼 있으면 Flask로, 없으면 표준 라이브러리 서버로 자동 구동됩니다.

## 4. 실제 공공데이터 연동
1. [서울 열린데이터광장](https://data.seoul.go.kr)에서 인증키 발급
2. `SEOUL_API_KEY=발급키 python data/etl.py`
3. `data/etl.py`의 `try_fetch_seoul_api()`에서 실제 응답 파싱 로직을 채우면 샘플 대신 실데이터가 적재됩니다.

## 5. DB 직접 조회
```bash
sqlite3 db/housing.db "SELECT * FROM v_property_latest_risk LIMIT 5;"
sqlite3 db/housing.db "SELECT risk_grade, COUNT(*) FROM v_property_latest_risk GROUP BY risk_grade;"
```

## 6. 위험도 기준 바꾸기
`data/risk_engine.py`의 `GRADE_BANDS`(등급 임계치)와 `assess()`(점수 공식),
`data/etl.py`의 `FINANCE_PRODUCTS`(상품·금리)를 수정한 뒤 위 2번 절차를 다시 실행하세요.

## 폴더 요약
- `db/` — 스키마 & 생성된 SQLite
- `data/` — ETL·위험도 엔진·빌드 스크립트
- `backend/` — REST API
- `frontend/` — 지도/도표 UI (라이브 + 배포판)
