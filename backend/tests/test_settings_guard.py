from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2"])
def test_none_mode_rejects_non_local_host(host: str, make_settings: Any) -> None:
    with pytest.raises(ValueError, match="AUTH_MODE=none"):
        make_settings(AUTH_MODE="none", APP_HOST=host)


def test_access_code_requires_code(make_settings: Any) -> None:
    with pytest.raises(ValueError, match="ACCESS_CODE"):
        make_settings(AUTH_MODE="access_code")


def test_fake_llm_only_in_development(make_settings: Any) -> None:
    with pytest.raises(ValueError, match="LLM_PROVIDER=fake"):
        make_settings(AUTH_MODE="access_code", ACCESS_CODE="x", APP_HOST="0.0.0.0")


def test_real_llm_requires_key(make_settings: Any) -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_settings(LLM_PROVIDER="anthropic")


def test_server_refuses_to_start_exposed_without_auth(tmp_path: Path) -> None:
    """Startup test (§11): AUTH_MODE=none with APP_HOST=0.0.0.0 must refuse to start."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("APP_", "AUTH_", "LLM_", "DATABASE_"))},
        "AUTH_MODE": "none",
        "APP_HOST": "0.0.0.0",
        "LLM_PROVIDER": "fake",
        "FILE_ENCRYPTION_KEY": "S4VQ6p8V8KHe-Ff6MfmrnJu2mP1bH3m0ElxTnW90sc8=",
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app"], cwd=tmp_path, env={**env, "PYTHONPATH": str(BACKEND)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "AUTH_MODE=none" in result.stderr
