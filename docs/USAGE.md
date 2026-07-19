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

## 4. 실제 공공데이터(서울시 실거래가) 연동
1. [서울 열린데이터광장](https://data.seoul.go.kr)에서 인증키 발급
   (사용 데이터셋: [OA-21275 부동산 실거래가](https://data.seoul.go.kr/dataList/OA-21275/S/1/datasetView.do),
   [OA-21276 부동산 전월세가](https://data.seoul.go.kr/dataList/OA-21276/S/1/datasetView.do))
2. `SEOUL_API_KEY=발급키 python data/etl.py` 실행
   - `data/seoul_api.py`가 매매(`tbLnOpendataRtms`)·전월세(`tbLnOpendataRentV`) API를
     실제로 호출해 자치구별 평균 매매가·전세가를 산출하고, `districts` 테이블을 갱신합니다.
   - 수집한 원본 거래는 `transactions` 테이블에 `source='seoul_open_data'` 로 적재됩니다.
   - API 키가 없거나 호출이 실패하면 자동으로 FALLBACK 근사값을 사용합니다 (서비스 중단 없음).
3. **컬럼명이 맞지 않는 경우:** 서울 열린데이터광장 API의 정확한 응답 컬럼명은 발급받은 키로
   직접 호출해봐야 확정됩니다. 만약 실행 로그에 `응답에서 컬럼을 하나도 매칭하지 못했습니다`
   메시지가 뜨면, 함께 출력되는 `샘플 응답 키` 목록을 확인해 `data/seoul_api.py`의
   `FIELD_CANDIDATES` 딕셔너리에 실제 키를 추가하세요. (짐작으로 임의 필드를 만들지 않고,
   실패 시 안전하게 FALLBACK 하도록 설계했습니다.)
4. 연동 후에는 3번 절차(`export_json.py` → `build_standalone.py`)를 다시 실행해
   실데이터를 반영한 `standalone.html`을 재빌드하세요.

## 5. KB국민은행 금융상품 매칭 확인
```bash
python data/risk_engine.py       # 위험도 3개 샘플 + 매칭된 KB 상품/사유 출력
```
`data/kb_products.py`의 `match_product()`가 위험등급과 보증금 규모를 함께 보고
청년전용 버팀목 전세자금대출 / KB 청년 맞춤형 전세자금대출 / KB스타 전세자금대출(HUG) /
전세보증금반환보증 안내 중 하나를 매칭합니다. 상품 정보·매칭 로직을 바꾸려면 이 파일의
`PRODUCTS`와 `match_product()`를 수정한 뒤 `python data/etl.py`를 다시 실행하세요.

## 6. DB 직접 조회
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('db/housing.db')
for r in c.execute('SELECT risk_grade, recommended_product, COUNT(*) FROM v_property_latest_risk GROUP BY 1,2'):
    print(r)
"
```

## 7. 위험도 기준 바꾸기
`data/risk_engine.py`의 `GRADE_BANDS`(등급 임계치)와 `assess()`(점수 공식)를 수정한 뒤
위 2번 절차를 다시 실행하세요.

## 폴더 요약
- `db/` — 스키마 & 생성된 SQLite
- `data/` — ETL·서울시 API 연동·위험도 엔진·KB상품 매칭·빌드 스크립트
- `backend/` — REST API
- `frontend/` — 지도/도표 UI (라이브 + 배포판)
