"""
export_json.py — DB 내용을 프론트엔드/프로토타입 임베드용 단일 JSON으로 내보낸다.
결과: frontend/data.json  (지도·도표에 바로 사용)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import app  # noqa: E402

def get_products():
    with app._conn() as c:
        rows = c.execute("SELECT * FROM finance_products").fetchall()
    return {r["product_code"]: dict(r) for r in rows}


out = {
    "properties": app.get_properties(),
    "districts": app.get_districts(),
    "stats": app.get_stats(),
    "products": get_products(),
}
dest = ROOT / "frontend" / "data.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[export] {dest}  (매물 {len(out['properties'])}건)")
