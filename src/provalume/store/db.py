"""SQLite connection management, pragmas, and the migration chain.

One connection per process (ADR-0003: Provalume is single-writer). Every mutation
goes through an explicit transaction, so a crash can never leave a memory promoted
with no transition row recording why.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from provalume.errors import IntegrityError, SchemaVersionError, StoreError
from provalume.store.migrations import MIGRATIONS, SCHEMA_VERSION

#: Pragmas asserted on every connection, and re-checked by ``provalume audit`` so
#: a database opened by other tooling with different settings is detectable.
EXPECTED_PRAGMAS: Final[dict[str, str | int]] = {
    "journal_mode": "wal",
    "foreign_keys": 1,
    "synchronous": 1,  # NORMAL: durable across process crashes, which is the
    # failure mode that matters for a rebuildable cache over an append-only
    # journal. FULL would add an fsync per commit to also survive OS crashes.
}

BUSY_TIMEOUT_MS: Final = 5_000

MEMORY_PATH: Final = ":memory:"


class Database:
    """Owns the SQLite connection and the migration chain."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path) if path != MEMORY_PATH else Path(MEMORY_PATH)
        self.read_only = read_only
        self._in_tx = False

        if str(path) != MEMORY_PATH:
            self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # isolation_level=None puts transaction control in our hands rather
            # than sqlite3's implicit-BEGIN heuristics, which are surprising and
            # would silently split a promotion from its transition row.
            self._conn = sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error as exc:  # pragma: no cover - environment dependent
            msg = f"cannot open database at {path}: {exc}"
            raise StoreError(msg) from exc

        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._check_fts5()
        if not read_only:
            self._migrate()
        else:
            self._assert_version_supported(self.schema_version())

    # -- setup -------------------------------------------------------------

    def _apply_pragmas(self) -> None:
        conn = self._conn
        if str(self.path) != MEMORY_PATH:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        # Defence against a tampered schema executing something at open time.
        with contextlib.suppress(sqlite3.Error):  # pragma: no cover - older SQLite
            conn.execute("PRAGMA trusted_schema=OFF")

    def _check_fts5(self) -> None:
        """Fail at open with a clear message rather than at first query."""
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE temp.provalume_fts_probe USING fts5(x)"
            )
            self._conn.execute("DROP TABLE temp.provalume_fts_probe")
        except sqlite3.Error as exc:
            msg = (
                "this Python build's SQLite lacks FTS5, which Provalume requires "
                f"for retrieval ({exc}). Install a CPython build with FTS5 enabled, "
                "or use the python.org macOS installer / a distro python3 package."
            )
            raise StoreError(msg) from exc

    def _assert_version_supported(self, current: int) -> None:
        if current > SCHEMA_VERSION:
            msg = (
                f"database schema version {current} is newer than this build of "
                f"Provalume supports ({SCHEMA_VERSION}). Upgrade Provalume; "
                "operating on an unknown schema would corrupt data."
            )
            raise SchemaVersionError(msg)

    def _migrate(self) -> None:
        conn = self._conn
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = int(row["version"]) if row else 0
        self._assert_version_supported(current)
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")

        for index in range(current, len(MIGRATIONS)):
            version = index + 1
            # executescript() commits any pending transaction first, so atomicity
            # comes from the BEGIN/COMMIT embedded here. `version` is a loop
            # integer, never external input.
            # `version` is a loop integer over a module-level migration list,
            # never external input. The migration bodies are literals in
            # provalume.store.migrations.
            script = (
                "BEGIN IMMEDIATE;\n"  # nosec B608
                f"{MIGRATIONS[index]}\n"
                f"UPDATE schema_version SET version = {version};\n"
                "COMMIT;"
            )
            try:
                conn.executescript(script)
            except sqlite3.Error as exc:
                with_rollback = ""
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    with_rollback = " (no transaction to roll back)"
                msg = f"migration to schema version {version} failed{with_rollback}: {exc}"
                raise StoreError(msg) from exc

    # -- introspection -----------------------------------------------------

    def schema_version(self) -> int:
        try:
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        except sqlite3.Error:
            return 0
        return int(row["version"]) if row else 0

    def pragma_report(self) -> dict[str, Any]:
        """Actual pragma values, for ``doctor`` and ``audit``."""
        out: dict[str, Any] = {}
        for name in ("journal_mode", "foreign_keys", "synchronous", "busy_timeout"):
            row = self._conn.execute(f"PRAGMA {name}").fetchone()
            out[name] = row[0] if row else None
        return out

    def check_pragmas(self) -> list[str]:
        """Return descriptions of any pragma that is not as expected."""
        problems: list[str] = []
        actual = self.pragma_report()
        for name, expected in EXPECTED_PRAGMAS.items():
            got = actual.get(name)
            if name == "journal_mode":
                if str(self.path) == MEMORY_PATH:
                    continue  # in-memory databases cannot use WAL
                if str(got).lower() != str(expected).lower():
                    problems.append(f"journal_mode is {got!r}, expected 'wal'")
            elif int(got or 0) != int(expected):
                problems.append(f"{name} is {got!r}, expected {expected!r}")
        return problems

    def integrity_check(self) -> list[str]:
        """SQLite's own structural check. Empty means healthy."""
        rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        results = [str(r[0]) for r in rows]
        return [] if results == ["ok"] else results

    def foreign_key_check(self) -> list[str]:
        rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        return [f"{r[0]}: rowid={r[1]} references {r[2]}" for r in rows]

    # -- transactions ------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """An explicit write transaction.

        Nesting reuses the outer transaction rather than raising, so a writer can
        call another writer without either needing to know whether it is the
        outermost. ``BEGIN IMMEDIATE`` takes the write lock up front, which turns
        a lock conflict into a clean busy-timeout wait instead of a mid-transaction
        failure after work has been done.
        """
        if self.read_only:
            msg = "database opened read-only"
            raise StoreError(msg)
        if self._in_tx:
            yield self._conn
            return
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield self._conn
        except BaseException:
            with contextlib.suppress(sqlite3.Error):  # pragma: no cover
                self._conn.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False
        self._conn.execute("COMMIT")

    # -- queries -----------------------------------------------------------

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        """Run a statement. Values are always bound, never interpolated (T23)."""
        try:
            return self._conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            if "append-only" in str(exc):
                from provalume.errors import AppendOnlyViolation

                raise AppendOnlyViolation(str(exc)) from exc
            raise
        except sqlite3.OperationalError as exc:
            if "append-only" in str(exc):
                from provalume.errors import AppendOnlyViolation

                raise AppendOnlyViolation(str(exc)) from exc
            msg = f"query failed: {exc}"
            raise StoreError(msg) from exc

    def query(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def query_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.execute(sql, params).fetchone()
        return row

    def scalar(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    # -- lifecycle ---------------------------------------------------------

    def vacuum(self) -> None:
        if self._in_tx:
            msg = "cannot VACUUM inside a transaction"
            raise StoreError(msg)
        self._conn.execute("VACUUM")

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):  # pragma: no cover
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Raw connection, for the store layer only.

        Not public API: no caller outside :mod:`provalume.store` should hold this,
        because doing so bypasses the transaction and append-only discipline that
        the rest of the system's guarantees rest on.
        """
        return self._conn


def open_database(path: Path | str, *, read_only: bool = False) -> Database:
    """Open (and migrate, unless read-only) a Provalume database."""
    return Database(path, read_only=read_only)


def verify_healthy(db: Database) -> None:
    """Raise :class:`IntegrityError` if the database is structurally unsound."""
    problems = db.integrity_check()
    if problems:
        msg = "sqlite integrity_check failed: " + "; ".join(problems)
        raise IntegrityError(msg)
    fk = db.foreign_key_check()
    if fk:
        msg = "foreign key violations: " + "; ".join(fk)
        raise IntegrityError(msg)
