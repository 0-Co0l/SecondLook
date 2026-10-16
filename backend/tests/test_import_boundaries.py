from pathlib import Path
import re


def test_only_pdf_module_imports_pymupdf() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"^\s*(import fitz|from fitz import)", re.MULTILINE)
    offenders: list[str] = []
    for pyfile in app_dir.rglob("*.py"):
        if pyfile.name == "pdf.py":
            continue
        text = pyfile.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(pyfile))
    assert offenders == [], f"Unexpected PyMuPDF imports outside pdf.py: {offenders}"
