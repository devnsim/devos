# ==============================================================================
# FILE: app/modules/db_editor/engine.py
# OVERVIEW: Database Editor logic (no Qt): the user's SQLite databases (kept in the content library) and
#           everything the editor does to one - browse objects, read / edit rows, change the schema, run
#           SQL, read and set properties, check, compact and export.
# PURPOSE:
#   1. UserDatabases: list / create / import / rename / delete the user's databases (content kind
#      "database") plus the DevOS system database, opened read-only for inspection.
#   2. DbConnection: objects(), columns(), indexes(), foreign_keys(), row_count(), fetch_rows() (paged,
#      sorted, filtered, with row keys), update_cell(), insert_row(), delete_rows().
#   3. Schema: create_table(), rename_table(), drop_object(), add_column(), rename_column(), drop_column(),
#      create_index(); create_table_sql() previews the statement.
#   4. execute_script(): runs one or many statements, returns result sets and messages, stops at the
#      first error; properties(), set_pragma(), integrity_check(), vacuum(), backup_to().
# CONTRACT:
#   - Requires: sqlite3, app.clients.library, app.data.store.database (get_database),
#               app.data.activity_log
#   - Exposes: UserDatabases, DatabaseEntry, DbConnection, DbEditorError, ColumnDef, ColumnInfo, DbObject,
#              RowsPage, QueryResult, SYSTEM_DATABASE_ID, COLUMN_TYPES, quote_identifier(), coerce_value(),
#              split_statements(), is_sqlite_file()
# NOTES:
#   - [2026-11-17] Library access goes through the Knowledge engine (app.clients.library), not the content store (Phase 4).
#   - [2026-11-16] Created with the Database Editor module. User databases are library files (no save
#                  dialogs); the system database opens with mode=ro so the editor can never damage DevOS.
#                  Rows are addressed by rowid, or by primary key for WITHOUT ROWID tables; views are
#                  read-only. Schema changes and row inserts / deletes are announced in the log; single
#                  cell edits are debug-level.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - row key (dict): Column -> value that identifies one row ({"rowid": 7} or the primary-key columns).
# - SYSTEM_DATABASE_ID (int): Pseudo item id of the DevOS database (value: 0; always read-only).
# - affinity: SQLite's type family for a declared column type (INTEGER, REAL, TEXT, BLOB, NUMERIC).
# - MAX_RESULT_ROWS (int): Rows kept from one SELECT in the SQL console (default: 5000).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TYPES_AND_HELPERS
#    1.1 imports_and_types
#    1.2 helpers
# 2. USER_DATABASES
#    2.1 user_databases
# 3. DB_CONNECTION
#    3.1 open_and_objects
#    3.2 rows
#    3.3 schema
#    3.4 sql_and_properties
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.data.activity_log import get_activity_log
from app.clients.library import library
from app.data.store.database import get_database

SYSTEM_DATABASE_ID = 0
SYSTEM_DATABASE_NAME = "DevOS system database (read-only)"
MAX_RESULT_ROWS = 5000
COLUMN_TYPES = ("INTEGER", "REAL", "TEXT", "BLOB", "NUMERIC", "BOOLEAN", "DATE", "DATETIME")
SQLITE_HEADER = b"SQLite format 3\x00"
OBJECT_TYPES = ("table", "view", "index", "trigger")


class DbEditorError(Exception):
    """A database action that failed; the message is meant for the user."""


@dataclass
class DatabaseEntry:
    item_id: int
    name: str
    path: Path
    is_read_only: bool
    size_bytes: int


@dataclass
class ColumnInfo:
    name: str
    declared_type: str
    is_not_null: bool
    default: str | None
    primary_key_position: int                  # 0 = not part of the primary key


@dataclass
class ColumnDef:
    """A column to create (Create table / Add column forms)."""
    name: str
    declared_type: str = "TEXT"
    is_primary_key: bool = False
    is_not_null: bool = False
    is_unique: bool = False
    default: str = ""                          # SQL literal / expression; "" = none


@dataclass
class DbObject:
    name: str
    object_type: str                           # "table", "view", "index", "trigger"
    table_name: str
    sql: str


@dataclass
class RowsPage:
    columns: list[str]
    declared_types: list[str]
    rows: list[list[Any]]
    keys: list[dict | None]                    # one per row; None = row can't be edited
    total_rows: int
    is_editable: bool


@dataclass
class QueryResult:
    statement: str
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    is_truncated: bool = False

    @property
    def has_rows(self) -> bool:
        return bool(self.columns)
# --- END [1.1] ---

# ==============================================================================
# [1.2] helpers
# ==============================================================================
def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def affinity(declared_type: str) -> str:
    """SQLite's affinity rules (https://sqlite.org/datatype3.html, section 3.1)."""
    upper = (declared_type or "").upper()
    if "INT" in upper:
        return "INTEGER"
    if any(word in upper for word in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not upper or "BLOB" in upper:
        return "BLOB"
    if any(word in upper for word in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def coerce_value(text: str | None, declared_type: str) -> Any:
    """Typed value for text typed into a cell / form field. None stays NULL; numbers become numbers in
    numeric columns (anything that isn't a number is kept as text, as SQLite would)."""
    if text is None:
        return None
    column_affinity = affinity(declared_type)
    if column_affinity == "TEXT":
        return text
    stripped = text.strip()
    if stripped == "" and column_affinity != "BLOB":
        return None
    try:
        return int(stripped)
    except ValueError:
        try:
            return float(stripped)
        except ValueError:
            return text


def split_statements(sql: str) -> list[str]:
    """Split a script into statements (semicolons inside strings, comments and triggers are respected)."""
    statements, current = [], ""
    for character in sql:
        current += character
        if character == ";" and sqlite3.complete_statement(current):
            if current.strip(" \t\r\n;"):
                statements.append(current.strip())
            current = ""
    if current.strip():
        statements.append(current.strip())
    return statements


def is_sqlite_file(path: str | Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == SQLITE_HEADER
    except OSError:
        return False


def column_sql(column: ColumnDef) -> str:
    parts = [quote_identifier(column.name.strip())]
    if column.declared_type.strip():
        parts.append(column.declared_type.strip())
    if column.is_primary_key:
        parts.append("PRIMARY KEY")
    if column.is_not_null:
        parts.append("NOT NULL")
    if column.is_unique and not column.is_primary_key:
        parts.append("UNIQUE")
    if column.default.strip():
        parts.append(f"DEFAULT {column.default.strip()}")
    return " ".join(parts)


def create_table_sql(name: str, columns: list[ColumnDef]) -> str:
    """CREATE TABLE statement for the form (several PK columns -> one table-level PRIMARY KEY)."""
    keyed = [column for column in columns if column.is_primary_key]
    composite = len(keyed) > 1
    lines = [column_sql(ColumnDef(column.name, column.declared_type, column.is_primary_key and not composite,
                                  column.is_not_null, column.is_unique, column.default)) for column in columns]
    if composite:
        lines.append("PRIMARY KEY (" + ", ".join(quote_identifier(column.name) for column in keyed) + ")")
    return f"CREATE TABLE {quote_identifier(name.strip())} (\n    " + ",\n    ".join(lines) + "\n)"
# --- END [1.2] ---


# ==============================================================================
# [2.1] user_databases
# ==============================================================================
class UserDatabases:
    """The user's databases (content library, kind "database") + the read-only system database."""

    KIND = "database"

    def __init__(self) -> None:
        self.store = library()
        self.log = get_activity_log()

    def entries(self) -> list[DatabaseEntry]:
        system_path = get_database().path
        entries = [DatabaseEntry(item.item_id, item.name, self.store.path(item), False, item.size_bytes)
                   for item in sorted(self.store.items(self.KIND), key=lambda item: item.name.lower())]
        entries.append(DatabaseEntry(SYSTEM_DATABASE_ID, SYSTEM_DATABASE_NAME, system_path, True,
                                     system_path.stat().st_size if system_path.exists() else 0))
        return entries

    def entry(self, item_id: int) -> DatabaseEntry | None:
        return next((entry for entry in self.entries() if entry.item_id == item_id), None)

    def create(self, name: str) -> DatabaseEntry:
        name = self.store.unique_name(self.KIND, name.strip() or "New database")
        path = self.store.new_path(self.KIND, ".sqlite3")
        with self.log.step("db_editor", f"Create database “{name}”"):
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 0")       # writes the header: a valid, empty database
            connection.commit()
            connection.close()
            item = self.store.register_path(self.KIND, name, path, {"created_by": "Database Editor"})
        return self.entry(item.item_id)

    def import_file(self, path: str | Path) -> DatabaseEntry:
        path = Path(path)
        with self.log.step("db_editor", f"Import database {path.name}"):
            if not is_sqlite_file(path):
                raise DbEditorError(f"{path.name} is not an SQLite database")
            item = self.store.add_file(self.KIND, path)
        return self.entry(item.item_id)

    def rename(self, item_id: int, name: str) -> str:
        if item_id == SYSTEM_DATABASE_ID:
            raise DbEditorError("The system database can't be renamed")
        return self.store.rename(item_id, name)

    def delete(self, item_id: int) -> None:
        if item_id == SYSTEM_DATABASE_ID:
            raise DbEditorError("The system database can't be deleted")
        self.store.delete(item_id)

    def export(self, item_id: int, destination: str | Path) -> Path:
        """Consistent copy (SQLite backup API, safe while open) to a place the user picked."""
        entry = self.entry(item_id)
        with self.log.step("db_editor", f"Export “{entry.name}”"):
            connection = DbConnection(entry)
            try:
                connection.backup_to(destination)
            finally:
                connection.close()
        return Path(destination)

    def touch(self, item_id: int) -> None:
        """Keep the library's size / modified time in step after edits."""
        if item_id != SYSTEM_DATABASE_ID:
            self.store.refresh_size(item_id)
# --- END [2.1] ---


# ==============================================================================
# [3.1] open_and_objects
# ==============================================================================
class DbConnection:
    """One open database. All writes autocommit (explicit BEGIN / COMMIT in the SQL console still work)."""

    def __init__(self, entry: DatabaseEntry) -> None:
        self.entry = entry
        self.log = get_activity_log()
        try:
            if entry.is_read_only:
                self.connection = sqlite3.connect(f"file:{entry.path}?mode=ro", uri=True, isolation_level=None)
            else:
                self.connection = sqlite3.connect(entry.path, isolation_level=None)
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("SELECT count(*) FROM sqlite_master").fetchone()   # fails early if not a database
        except sqlite3.Error as error:
            raise DbEditorError(f"Can't open “{entry.name}”: {error}") from error

    @property
    def is_read_only(self) -> bool:
        return self.entry.is_read_only

    def close(self) -> None:
        self.connection.close()

    def _query(self, sql: str, parameters: tuple = ()) -> list[tuple]:
        try:
            return self.connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise DbEditorError(str(error)) from error

    def _write(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        if self.is_read_only:
            raise DbEditorError(f"“{self.entry.name}” is read-only")
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as error:
            raise DbEditorError(str(error)) from error

    def objects(self, object_type: str | None = None) -> list[DbObject]:
        rows = self._query("SELECT name, type, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                           "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name COLLATE NOCASE")
        return [DbObject(*row) for row in rows if object_type is None or row[1] == object_type]

    def tables(self) -> list[str]:
        return [item.name for item in self.objects("table")]

    def object_type(self, name: str) -> str | None:
        row = self._query("SELECT type FROM sqlite_master WHERE name = ?", (name,))
        return row[0][0] if row else None

    def columns(self, table: str) -> list[ColumnInfo]:
        rows = self._query(f"PRAGMA table_info({quote_identifier(table)})")
        return [ColumnInfo(row[1], row[2] or "", bool(row[3]), row[4], row[5]) for row in rows]

    def indexes(self, table: str) -> list[tuple[str, bool, list[str]]]:
        """(name, is_unique, columns) per index on a table."""
        result = []
        for row in self._query(f"PRAGMA index_list({quote_identifier(table)})"):
            columns = [info[2] for info in self._query(f"PRAGMA index_info({quote_identifier(row[1])})")]
            result.append((row[1], bool(row[2]), columns))
        return result

    def foreign_keys(self, table: str) -> list[tuple[str, str, str]]:
        """(column, referenced table, referenced column)."""
        return [(row[3], row[2], row[4] or "") for row in self._query(f"PRAGMA foreign_key_list({quote_identifier(table)})")]

    def object_sql(self, name: str) -> str:
        row = self._query("SELECT COALESCE(sql, '') FROM sqlite_master WHERE name = ?", (name,))
        return row[0][0] if row else ""

    def row_count(self, table: str, where: str = "") -> int:
        clause = f" WHERE {where}" if where.strip() else ""
        return self._query(f"SELECT count(*) FROM {quote_identifier(table)}{clause}")[0][0]
# --- END [3.1] ---

# ==============================================================================
# [3.2] rows
# ==============================================================================
    def _has_rowid(self, table: str) -> bool:
        if self.object_type(table) != "table":
            return False
        try:
            self.connection.execute(f"SELECT rowid FROM {quote_identifier(table)} LIMIT 0")
            return True
        except sqlite3.Error:
            return False

    def fetch_rows(self, table: str, limit: int = 200, offset: int = 0, order_by: str | None = None,
                   descending: bool = False, where: str = "") -> RowsPage:
        """One page of a table or view, with a key per row so edits can find it again."""
        columns = self.columns(table)
        names = [column.name for column in columns]
        is_table = self.object_type(table) == "table"
        has_rowid = self._has_rowid(table)
        key_columns = [] if has_rowid else [column.name for column in sorted(columns, key=lambda c: c.primary_key_position)
                                            if column.primary_key_position]
        is_editable = is_table and not self.is_read_only and (has_rowid or bool(key_columns))
        select = ("rowid, " if has_rowid else "") + (", ".join(quote_identifier(name) for name in names) or "*")
        clause = f" WHERE {where}" if where.strip() else ""
        order = f" ORDER BY {quote_identifier(order_by)} {'DESC' if descending else 'ASC'}" if order_by in names else ""
        rows = self._query(f"SELECT {select} FROM {quote_identifier(table)}{clause}{order} LIMIT ? OFFSET ?", (limit, offset))
        keys: list[dict | None] = []
        values: list[list[Any]] = []
        for row in rows:
            row = list(row)
            if has_rowid:
                keys.append({"rowid": row.pop(0)})
            elif key_columns:
                keys.append({name: row[names.index(name)] for name in key_columns})
            else:
                keys.append(None)
            values.append(row)
        return RowsPage(names, [column.declared_type for column in columns], values,
                        keys if is_editable else [None] * len(values), self.row_count(table, where), is_editable)

    @staticmethod
    def _key_clause(key: dict) -> tuple[str, tuple]:
        parts = [f"{quote_identifier(name) if name != 'rowid' else 'rowid'} IS ?" for name in key]
        return " AND ".join(parts), tuple(key.values())

    def update_cell(self, table: str, key: dict, column: str, value: Any) -> None:
        clause, parameters = self._key_clause(key)
        cursor = self._write(f"UPDATE {quote_identifier(table)} SET {quote_identifier(column)} = ? WHERE {clause}",
                             (value, *parameters))
        if cursor.rowcount != 1:
            raise DbEditorError(f"Row not found in {table} (changed elsewhere?)")
        self.log.debug("db_editor", f"{self.entry.name} › {table}: set {column}")

    def update_row(self, table: str, key: dict, values: dict) -> None:
        if not values:
            return
        clause, parameters = self._key_clause(key)
        assignments = ", ".join(f"{quote_identifier(name)} = ?" for name in values)
        self._write(f"UPDATE {quote_identifier(table)} SET {assignments} WHERE {clause}", (*values.values(), *parameters))
        self.log.info("db_editor", f"{self.entry.name} › {table}: saved a row ({len(values)} field(s))")

    def insert_row(self, table: str, values: dict | None = None) -> dict | None:
        """Insert a row (defaults for missing columns); returns its key."""
        values = values or {}
        if values:
            names = ", ".join(quote_identifier(name) for name in values)
            marks = ", ".join("?" for _ in values)
            cursor = self._write(f"INSERT INTO {quote_identifier(table)} ({names}) VALUES ({marks})", tuple(values.values()))
        else:
            cursor = self._write(f"INSERT INTO {quote_identifier(table)} DEFAULT VALUES")
        self.log.info("db_editor", f"{self.entry.name} › {table}: added a row")
        return {"rowid": cursor.lastrowid} if self._has_rowid(table) else None

    def delete_rows(self, table: str, keys: list[dict]) -> int:
        deleted = 0
        try:
            self.connection.execute("BEGIN")
            for key in keys:
                clause, parameters = self._key_clause(key)
                deleted += self._write(f"DELETE FROM {quote_identifier(table)} WHERE {clause}", parameters).rowcount
            self.connection.execute("COMMIT")
        except (sqlite3.Error, DbEditorError):
            self.connection.execute("ROLLBACK")
            raise
        self.log.info("db_editor", f"{self.entry.name} › {table}: deleted {deleted} row(s)")
        return deleted
# --- END [3.2] ---

# ==============================================================================
# [3.3] schema
# ==============================================================================
    def _schema_change(self, description: str, sql: str) -> None:
        self._write(sql)
        self.log.success("db_editor", f"{self.entry.name}: {description}")

    def create_table(self, name: str, columns: list[ColumnDef]) -> None:
        if not name.strip():
            raise DbEditorError("The table needs a name")
        if not columns or any(not column.name.strip() for column in columns):
            raise DbEditorError("Every column needs a name")
        self._schema_change(f"created table {name}", create_table_sql(name, columns))

    def rename_table(self, old: str, new: str) -> None:
        self._schema_change(f"renamed table {old} → {new}",
                            f"ALTER TABLE {quote_identifier(old)} RENAME TO {quote_identifier(new.strip())}")

    def drop_object(self, object_type: str, name: str) -> None:
        if object_type not in OBJECT_TYPES:
            raise DbEditorError(f"Can't drop a {object_type}")
        self._schema_change(f"dropped {object_type} {name}", f"DROP {object_type.upper()} {quote_identifier(name)}")

    def add_column(self, table: str, column: ColumnDef) -> None:
        self._schema_change(f"added column {table}.{column.name}",
                            f"ALTER TABLE {quote_identifier(table)} ADD COLUMN {column_sql(column)}")

    def rename_column(self, table: str, old: str, new: str) -> None:
        self._schema_change(f"renamed column {table}.{old} → {new}",
                            f"ALTER TABLE {quote_identifier(table)} RENAME COLUMN {quote_identifier(old)} TO {quote_identifier(new.strip())}")

    def drop_column(self, table: str, column: str) -> None:
        self._schema_change(f"dropped column {table}.{column}",
                            f"ALTER TABLE {quote_identifier(table)} DROP COLUMN {quote_identifier(column)}")

    def create_index(self, name: str, table: str, columns: list[str], is_unique: bool = False) -> None:
        listed = ", ".join(quote_identifier(column) for column in columns)
        self._schema_change(f"created index {name}",
                            f"CREATE {'UNIQUE ' if is_unique else ''}INDEX {quote_identifier(name)} "
                            f"ON {quote_identifier(table)} ({listed})")
# --- END [3.3] ---

# ==============================================================================
# [3.4] sql_and_properties
# ==============================================================================
    def execute_script(self, sql: str) -> tuple[list[QueryResult], str | None]:
        """Run each statement in turn; stop at the first error. Returns (results, error message or None)."""
        results: list[QueryResult] = []
        for number, statement in enumerate(split_statements(sql), start=1):
            started = time.perf_counter()
            try:
                cursor = self.connection.execute(statement)
                result = QueryResult(statement)
                if cursor.description:
                    result.columns = [description[0] for description in cursor.description]
                    result.rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
                    result.is_truncated = len(result.rows) > MAX_RESULT_ROWS
                    result.rows = result.rows[:MAX_RESULT_ROWS]
                else:
                    result.affected_rows = max(cursor.rowcount, 0)
                result.elapsed_ms = (time.perf_counter() - started) * 1000
                results.append(result)
            except sqlite3.Error as error:
                message = f"Statement {number}: {error}"
                self.log.error("db_editor", f"{self.entry.name}: SQL failed — {message}")
                return results, message
        self.log.info("db_editor", f"{self.entry.name}: ran {len(results)} SQL statement(s)")
        return results, None

    def properties(self) -> dict[str, Any]:
        def pragma(name: str) -> Any:
            return self._query(f"PRAGMA {name}")[0][0]
        counts = {object_type: 0 for object_type in OBJECT_TYPES}
        for item in self.objects():
            counts[item.object_type] = counts.get(item.object_type, 0) + 1
        page_size, page_count = pragma("page_size"), pragma("page_count")
        return {
            "Name": self.entry.name, "File": str(self.entry.path),
            "Access": "read-only" if self.is_read_only else "read / write",
            "Size": page_size * page_count, "SQLite version": self._query("SELECT sqlite_version()")[0][0],
            "Tables": counts["table"], "Views": counts["view"], "Indexes": counts["index"], "Triggers": counts["trigger"],
            "Page size": page_size, "Pages": page_count, "Free pages": pragma("freelist_count"),
            "Encoding": pragma("encoding"), "Journal mode": pragma("journal_mode"),
            "Auto-vacuum": {0: "none", 1: "full", 2: "incremental"}.get(pragma("auto_vacuum"), "?"),
            "User version": pragma("user_version"), "Application id": pragma("application_id"),
        }

    def set_pragma(self, name: str, value: Any) -> None:
        if name not in ("user_version", "application_id", "journal_mode"):
            raise DbEditorError(f"PRAGMA {name} can't be set here")
        self._write(f"PRAGMA {name} = {int(value) if name != 'journal_mode' else str(value)}")
        self.log.info("db_editor", f"{self.entry.name}: {name} set to {value}")

    def integrity_check(self) -> list[str]:
        problems = [row[0] for row in self._query("PRAGMA integrity_check")]
        if problems == ["ok"]:
            self.log.success("db_editor", f"{self.entry.name}: integrity check passed")
        else:
            self.log.warning("db_editor", f"{self.entry.name}: integrity check found {len(problems)} problem(s)")
        return problems

    def vacuum(self) -> None:
        before = self.entry.path.stat().st_size
        self._write("VACUUM")
        after = self.entry.path.stat().st_size
        self.log.success("db_editor", f"{self.entry.name}: compacted {before:,} → {after:,} bytes")

    def backup_to(self, destination: str | Path) -> None:
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()
# --- END [3.4] ---
