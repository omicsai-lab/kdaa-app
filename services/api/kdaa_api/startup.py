"""Bounded database wait used by container startup, before migrations."""
import sys
import time
from sqlalchemy import create_engine, text
from .config import Settings

def main():
    settings = Settings()
    settings.validate()
    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        for attempt in range(20):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception:
                if attempt == 19:
                    print("Database unavailable after bounded retry. Check PostgreSQL and DATABASE_URL.", file=sys.stderr)
                    raise SystemExit(1)
                time.sleep(min(0.5 * (attempt+1), 2))
    finally:
        engine.dispose()

if __name__ == "__main__":
    main()
