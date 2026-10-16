import importlib
import os

import pytest


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2"])
def test_none_mode_rejects_non_local_host(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("APP_HOST", host)
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", "S4VQ6p8V8KHe-Ff6MfmrnJu2mP1bH3m0ElxTnW90sc8=")
    with pytest.raises(Exception):
        importlib.reload(importlib.import_module("app.settings"))


def test_access_code_requires_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "access_code")
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", "S4VQ6p8V8KHe-Ff6MfmrnJu2mP1bH3m0ElxTnW90sc8=")
    with pytest.raises(Exception):
        importlib.reload(importlib.import_module("app.settings"))
