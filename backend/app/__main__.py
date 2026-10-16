"""Container and production entry point: `python -m app`.

Runs migrations (`alembic upgrade head`), then serves on APP_HOST:APP_PORT. Binding from settings, rather
than a hard-coded --host flag, is what makes the AUTH_MODE=none loopback guard (§4.6) effective.
"""

from __future__ import annotations

import uvicorn

from .db import run_migrations
from .main import configure_logging
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    run_migrations(settings.database_url)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.app_host,
        port=settings.app_port,
        proxy_headers=True,  # trusted proxies come from FORWARDED_ALLOW_IPS
        log_level=settings.log_level.lower(),
        server_header=False,
    )


if __name__ == "__main__":
    main()
