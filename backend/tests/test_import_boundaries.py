"""CI check (§4.8): only app/pdf.py may import PyMuPDF."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FORBIDDEN = {"fitz", "pymupdf"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in {
            "import_module",
            "__import__",
        }:
            found.update(a.value.split(".")[0] for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return found


def test_only_pdf_module_imports_pymupdf() -> None:
    offenders = [
        str(path.relative_to(BACKEND))
        for folder in ("app", "migrations")
        for path in (BACKEND / folder).rglob("*.py")
        if path.name != "pdf.py" and _imported_modules(path) & FORBIDDEN
    ]
    assert offenders == [], f"PyMuPDF imported outside app/pdf.py: {offenders}"
