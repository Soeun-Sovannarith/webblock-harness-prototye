"""Database engine and session management (SQLAlchemy 2.x).

SQLite is the zero-setup default for development and tests; set DATABASE_URL to a
PostgreSQL URL (``postgresql+psycopg://...``) in production.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)  # type: ignore[arg-type]

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from webblock_harness import models  # noqa: F401  (register tables)

        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Minimal forward migration for local databases: add new nullable/defaulted columns.

        Production deployments should use a real migration tool (see README).
        """
        from sqlalchemy import inspect, select, text, update

        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    ddl_type = column.type.compile(dialect=self.engine.dialect)
                    default = ""
                    if column.default is not None and getattr(column.default, "is_scalar", False):
                        value = column.default.arg  # type: ignore[attr-defined]
                        default = f" DEFAULT {('TRUE' if value else 'FALSE') if isinstance(value, bool) else repr(value)}"
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}{default}"))
                    if column.default is not None and getattr(column.default, "is_callable", False):
                        # Backfill per-row generated defaults (e.g. tenant ids) for rows that already exist.
                        pk = next(iter(table.primary_key.columns))
                        for (key,) in conn.execute(select(pk)).fetchall():
                            value = column.default.arg(None)  # type: ignore[attr-defined]
                            conn.execute(update(table).where(pk == key).values({column.name: value}))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
