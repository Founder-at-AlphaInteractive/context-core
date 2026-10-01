"""Wait until the database accepts connections.

Used as a docker-compose entrypoint step so the API does not race
Postgres startup.
"""

from __future__ import annotations

import sys
import time

from sqlalchemy import create_engine, text

from app.config import settings


def main() -> int:
    deadline = time.time() + 60
    last_error_message: str | None = None

    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        safe_url = engine.url.render_as_string(hide_password=True)
        print(f"Waiting for database at {safe_url}...")
    except Exception as exc:
        print(f"Invalid database URL configuration: {type(exc).__name__}", file=sys.stderr)
        return 1

    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("Database is ready!")
            engine.dispose()
            return 0
        except Exception as exc:  # noqa: BLE001
            # Redact raw password from exception message if present
            raw_msg = str(exc)
            if engine.url.password and engine.url.password in raw_msg:
                raw_msg = raw_msg.replace(engine.url.password, "******")
            last_error_message = f"{type(exc).__name__}: {raw_msg}"
            time.sleep(1.5)

    engine.dispose()
    print(f"Database was not ready in time: {last_error_message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
