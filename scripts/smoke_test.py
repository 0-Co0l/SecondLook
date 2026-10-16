"""Container smoke test (§11): /healthz, then upload a synthetic return and run a full review.

Usage: python scripts/smoke_test.py http://127.0.0.1:8000   (needs `pip install pymupdf` for the sample)
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from synthetic import make_return  # noqa: E402

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def request(method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait(predicate, timeout: float, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {what}")


def job_status(job_id: str) -> str:
    return json.loads(request("GET", f"/api/jobs/{job_id}")[1])["status"]


def main() -> None:
    wait(lambda: request("GET", "/healthz")[0] == 200, 180, "/healthz")
    print("healthz ok")

    boundary = uuid4().hex
    metadata = json.dumps({"tax_year": 2025, "questionnaire": {"paid_childcare": "yes"}})
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="return.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + make_return("text", 3).pdf + f"\r\n--{boundary}--\r\n".encode()
    status, payload = request("POST", "/api/jobs", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert status == 201, (status, payload)
    job_id = json.loads(payload)["job_id"]
    print("uploaded", job_id)

    wait(lambda: job_status(job_id) in {"awaiting_confirmation", "failed"}, 300, "redaction")
    assert job_status(job_id) == "awaiting_confirmation", job_status(job_id)
    assert request("POST", f"/api/jobs/{job_id}/confirm")[0] == 202
    wait(lambda: job_status(job_id) in {"done", "failed"}, 300, "review")
    assert job_status(job_id) == "done", request("GET", f"/api/jobs/{job_id}")[1]
    status, report = request("GET", f"/api/jobs/{job_id}/report.html")
    assert status == 200 and b"educational review aid" in report
    assert request("DELETE", f"/api/jobs/{job_id}")[0] == 204
    print("full review ok")


if __name__ == "__main__":
    main()
