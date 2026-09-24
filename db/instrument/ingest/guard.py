"""Shared session lock and fences for market ingestion, independent of backend.

The lock lives on the actual writing connection across commits. Connections are
never replaced or reconnected. Provider wrappers check the fence before calls;
the connection wrapper checks it before committing and reports changes only
after a successful commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from inspect import signature

logger = logging.getLogger(__name__)
INGEST_LOCK_KEY = 0x4C50524F464954  # stable project-wide signed bigint
ALL_RESOURCES = (
    "CN_INDEX_BARS", "CN_INDEX_FACTORS", "US_INDEX_BARS", "KR_INDEX_BARS",
    "CN_STOCK_DAILY", "CN_SECTOR_DAILY", "CN_STOCK_QUANT_INPUTS",
)


class IngestBusy(RuntimeError):
    """Another ingestion session owns the shared lock; no source was called."""


class IngestSessionLost(RuntimeError):
    """Writing session was lost. The collector must exit without reconnecting."""


class IngestOwnershipLost(RuntimeError):
    """The caller's lease/fence is unavailable or no longer belongs to it."""


FATAL_INGEST_ERRORS = (IngestSessionLost, IngestOwnershipLost)


def _disconnected(conn) -> bool:
    return any(isinstance(flag, (bool, int)) and bool(flag)
               for flag in (getattr(conn, "closed", False), getattr(conn, "broken", False)))


class IngestGuard:
    def __init__(self, conn, fence: Callable[[], bool | None] | None = None,
                 changed: Callable[[str], None] | None = None):
        self.conn = conn._ingest_raw_connection if isinstance(conn, _GuardedConnection) else conn
        self.fence = fence
        self.changed = changed
        self.acquired = False
        self._pid = None
        self._fatal: BaseException | None = None
        self.resources: tuple[str, ...] = ()
        self.connection = _GuardedConnection(self)

    def __enter__(self):
        if not self.try_acquire():
            raise IngestBusy("market ingestion is already running")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def _raise_connection_error(self, exc):
        state = getattr(exc, "sqlstate", None)
        if isinstance(exc, FATAL_INGEST_ERRORS):
            self._fatal = exc
        elif _disconnected(self.conn) or (isinstance(state, str) and state.startswith("08")):
            self._fatal = IngestSessionLost("market ingestion PG session disconnected")
        else:
            # psycopg network errors may have no SQLSTATE (server vanished).
            from psycopg import InterfaceError, OperationalError
            if isinstance(exc, (InterfaceError, OperationalError)) and state is None:
                self._fatal = IngestSessionLost("market ingestion PG session disconnected")
        if self._fatal is not None:
            raise self._fatal from exc

    def assert_alive(self):
        if self._fatal is not None:
            raise self._fatal
        if not self.acquired or _disconnected(self.conn):
            self._fatal = IngestSessionLost("market ingestion lock session is unavailable")
            raise self._fatal
        try:
            row = self.conn.execute("SELECT pg_backend_pid()").fetchone()
            if not row or row[0] != self._pid:
                self._fatal = IngestSessionLost("market ingestion PG session identity changed")
                raise self._fatal
        except Exception as exc:
            self._raise_connection_error(exc)
            raise
        if self.fence is not None:
            try:
                owned = self.fence()
                if owned is False:
                    raise IngestOwnershipLost("market ingestion lease no longer owned")
            except Exception as exc:
                self._fatal = exc if isinstance(exc, IngestOwnershipLost) else IngestOwnershipLost(
                    "market ingestion lease check unavailable")
                raise self._fatal from exc

    def try_acquire(self) -> bool:
        if self.acquired:
            self.assert_alive()
            return True  # do not increase PostgreSQL's reentrant lock count
        if _disconnected(self.conn):
            raise IngestSessionLost("market ingestion PG session is closed")
        try:
            row = self.conn.execute(
                "SELECT pg_try_advisory_lock(%s), pg_backend_pid()", (INGEST_LOCK_KEY,),
            ).fetchone()
            self.acquired = bool(row and row[0])
            self._pid = row[1] if self.acquired else None
            return self.acquired
        except Exception as exc:
            self._raise_connection_error(exc)
            raise

    def release(self):
        if not self.acquired:
            return
        try:
            if not _disconnected(self.conn):
                # Roll back any aborted/read/uncommitted unit before unlocking.
                self.conn.rollback()
                self.conn.execute("SELECT pg_advisory_unlock(%s)", (INGEST_LOCK_KEY,))
                # Leave a shared daily_job connection outside a read transaction.
                self.conn.rollback()
        except Exception as exc:
            self._raise_connection_error(exc)
            raise
        finally:
            self.acquired = False

    def rollback(self):
        try:
            self.conn.rollback()
        except Exception as exc:
            self._fatal = IngestSessionLost("market ingestion rollback failed; session unusable")
            raise self._fatal from exc
        self.assert_alive()

    def commit(self, *resources: str):
        self.assert_alive()
        try:
            self.conn.commit()
        except Exception as exc:
            self._raise_connection_error(exc)
            raise
        for resource in dict.fromkeys(resources or self.resources):
            if self.changed is not None:
                try:
                    self.changed(resource)
                except Exception:
                    # Committed facts survive a failed Redis notification.
                    logger.warning("market change notification failed: %s", resource, exc_info=True)

    def provider(self, provider):
        if provider is None:
            return None
        if isinstance(provider, _GuardedProvider):
            if provider._guard is not self:
                raise ValueError("provider belongs to another ingestion guard")
            return provider
        return _GuardedProvider(provider, self)


class _GuardedConnection:
    def __init__(self, guard):
        self._ingest_guard = guard
        self._ingest_raw_connection = guard.conn

    def __getattr__(self, name):
        return getattr(self._ingest_raw_connection, name)

    def execute(self, *args, **kwargs):
        self._ingest_guard.assert_alive()
        try:
            return self._ingest_raw_connection.execute(*args, **kwargs)
        except Exception as exc:
            self._ingest_guard._raise_connection_error(exc)
            raise

    def cursor(self, *args, **kwargs):
        self._ingest_guard.assert_alive()
        return _GuardedCursor(self._ingest_raw_connection.cursor(*args, **kwargs), self._ingest_guard)

    def commit(self):
        return self._ingest_guard.commit()

    def rollback(self):
        return self._ingest_guard.rollback()


class _GuardedCursor:
    def __init__(self, cursor, guard):
        self._cursor = cursor
        self._guard = guard

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        result = self._cursor.__exit__(exc_type, exc, tb)
        if exc is not None:
            self._guard._raise_connection_error(exc)
        return result

    def execute(self, *args, **kwargs):
        self._guard.assert_alive()
        try:
            return self._cursor.execute(*args, **kwargs)
        except Exception as exc:
            self._guard._raise_connection_error(exc)
            raise


class _GuardedProvider:
    def __init__(self, provider, guard):
        self._provider = provider
        self._guard = guard

    @property
    def __class__(self):
        # Preserve legacy isinstance(provider, BaseStockDataProvider) checks.
        return self._provider.__class__

    def __getattr__(self, name):
        self._guard.assert_alive()
        value = getattr(self._provider, name)
        if not callable(value):
            return value

        @wraps(value)
        def checked(*args, **kwargs):
            self._guard.assert_alive()
            try:
                return value(*args, **kwargs)
            except Exception as exc:
                self._guard._raise_connection_error(exc)
                raise
        return checked


def guarded_provider(conn, provider):
    """Guard providers constructed inside legacy private helper bodies."""
    active = getattr(conn, "_ingest_guard", None)
    return active.provider(provider) if isinstance(active, IngestGuard) else provider


@contextmanager
def ingestion_scope(conn, guard: IngestGuard | None = None):
    inherited = getattr(conn, "_ingest_guard", None)
    # Explicit type check: MagicMock attributes must never bypass locking.
    active = guard or (inherited if isinstance(inherited, IngestGuard) else None)
    owned = active is None
    active = active or IngestGuard(conn)
    raw = getattr(conn, "_ingest_raw_connection", None)
    raw = raw if isinstance(conn, _GuardedConnection) else conn
    if active.conn is not raw:
        raise ValueError("ingestion guard must own the actual writing connection")
    if owned and not active.try_acquire():
        raise IngestBusy("market ingestion is already running")
    try:
        active.assert_alive()
        yield active
    finally:
        if owned:
            active.release()


def is_ingest_lock_available(conn) -> bool:
    """A read-side probe only; callers still acquire again before ingestion."""
    guard = IngestGuard(conn)
    try:
        return guard.try_acquire()
    finally:
        guard.release()


def locked_ingestion(*resources: str):
    """Wrap a private collector; nested calls reuse the existing explicit guard.

    Wrapping both factories and existing providers ensures the first provider
    creation/source call occurs only after acquisition, including legacy CLIs.
    """
    def decorate(fn):
        sig = signature(fn)

        @wraps(fn)
        def wrapped(conn, *args, guard=None, **kwargs):
            with ingestion_scope(conn, guard) as active:
                bound = sig.bind(active.connection, *args, **kwargs)
                for name in ("provider", "fallback_provider"):
                    if bound.arguments.get(name) is not None:
                        bound.arguments[name] = active.provider(bound.arguments[name])
                for name in ("provider_factory", "fallback_provider_factory"):
                    factory = bound.arguments.get(name)
                    if factory is not None:
                        def checked_factory(factory=factory):
                            active.assert_alive()
                            return active.provider(factory())
                        bound.arguments[name] = checked_factory
                previous = active.resources
                active.resources = tuple(resources)
                try:
                    result = fn(*bound.args, **bound.kwargs)
                    active.assert_alive()
                    return result
                finally:
                    active.resources = previous
        return wrapped
    return decorate
