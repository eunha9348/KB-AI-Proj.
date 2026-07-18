"""
build_standalone.py — 데이터가 내장된 단일 HTML 프로토타입 생성.
frontend/_standalone_template.html 의 __DATA__ 자리에 frontend/data.json 을 주입해
frontend/standalone.html 을 만든다. (인터넷/백엔드 없이 파일 하나로 실행 가능)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tpl = (ROOT / "frontend" / "_standalone_template.html").read_text(encoding="utf-8")
data = (ROOT / "frontend" / "data.json").read_text(encoding="utf-8")

# </script> 가 데이터 안에 있으면 조기 종료되므로 이스케이프
data = data.replace("</", "<\\/")
html = tpl.replace("__DATA__", data)

dest = ROOT / "frontend" / "standalone.html"
dest.write_text(html, encoding="utf-8")
kb = len(html.encode("utf-8")) / 1024
print(f"[build] {dest}  ({kb:.0f} KB)")
