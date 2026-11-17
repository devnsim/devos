# ==============================================================================
# FILE: engines/data/tables.py
# OVERVIEW: The user's SQLite databases (Library kind "database") as Data collections: each table or view
#           is a collection "db:<item id>/<table>". Queries are compiled to SQL (engines/toolkit/sql.py), so
#           big tables page and filter in the database itself.
# PURPOSE:
#   1. table_names(path): the tables and views a database file offers.
#   2. SqliteTable: fields from the declared column types; rows keyed by rowid ("_rowid"); writes (update,
#      create, duplicate, delete, add column) checked against each column's kind. Views and WITHOUT ROWID
#      tables are read-only here (the Database Editor edits them).
# CONTRACT:
#   - Requires: sqlite3, engines.toolkit.sql, engines.data.common, engines.kernel (EngineError). The file's
#               path comes from Knowledge (Data never looks into the Library itself).
#   - Exposes: SqliteTable(ref, path, title), table_names(path), kind_for_type(declared), SQL_TYPES
# NOTES:
#   - [2026-11-17] Phase 5b: structure() (columns, indexes, foreign keys, SQL) and sql() for the SQL console.
#   - [2026-11-17] Columns are re-read when the file's schema_version changes (the Database Editor added one).
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SQL_TYPES (dict): Field kind -> the column type add_property declares.
# - BUSY_TIMEOUT_S (float): How long a write waits for another program's lock (default: 3).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TABLES
#    1.1 types
#    1.2 sqlite_table
# ==============================================================================

# ==============================================================================
# [1.1] types
# ==============================================================================
from __future__ import annotations

import sqlite3
from pathlib import Path

from engines.data.common import ROW_KEY, FileRef, Source, coerce, diff, field
from engines.kernel import EngineError
from engines.toolkit.sql import compile_query, quote_identifier, run_compiled

SQL_TYPES = {"text": "TEXT", "long_text": "TEXT", "select": "TEXT", "url": "TEXT", "number": "REAL",
             "integer": "INTEGER", "checkbox": "BOOLEAN", "date": "DATE", "datetime": "DATETIME"}
BUSY_TIMEOUT_S = 3.0


def kind_for_type(declared: str) -> str:
    """A field kind from a declared column type (SQLite's affinity rules, plus dates and booleans)."""
    upper = (declared or "").upper()
    if "BOOL" in upper:
        return "checkbox"
    if "DATETIME" in upper or "TIMESTAMP" in upper:
        return "datetime"
    if "DATE" in upper:
        return "date"
    if "INT" in upper:
        return "integer"
    if any(word in upper for word in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if "BLOB" in upper or not upper:
        return "json" if "BLOB" in upper else "text"
    return "number"                                     # REAL, FLOA, DOUB, NUMERIC, DECIMAL


def table_names(path: Path) -> list[tuple[str, str]]:
    """(name, "table" | "view") of a database file, internal sqlite_ tables left out."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [(row[0], row[1]) for row in connection.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")]
    finally:
        connection.close()
# --- END [1.1] ---


# ==============================================================================
# [1.2] sqlite_table
# ==============================================================================
class SqliteTable(Source):
    def __init__(self, ref: FileRef, path: Path, title: str) -> None:
        super().__init__(ref.collection)
        self.ref, self.path, self.title = ref, path, title
        self.connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)
        self.table = quote_identifier(ref.table)
        row = self.connection.execute("SELECT type, sql FROM sqlite_master WHERE name = ?", (ref.table,)).fetchone()
        if row is None:
            self.connection.close()
            raise EngineError(f"{title}: there is no table “{ref.table}”", code="not_found")
        self.is_view = row[0] == "view"
        self.is_editable = not self.is_view and "WITHOUT ROWID" not in (row[1] or "").upper()
        self._schema_version = None
        self._fresh()

    def close(self) -> None:
        self.connection.close()

    def _fresh(self) -> None:
        """Re-read the columns when the database's schema changed (another tool added or removed one)."""
        version = self.connection.execute("PRAGMA schema_version").fetchone()[0]
        if version == self._schema_version:
            return
        self._schema_version = version
        self.columns = [(info[1], info[2] or "", bool(info[3]), info[4], bool(info[5]))   # name, type, notnull, default, pk
                        for info in self.connection.execute(f"PRAGMA table_info({self.table})")]
        names = [name for name, *_rest in self.columns]
        self.kinds = {name: kind_for_type(declared) for name, declared, *_rest in self.columns}
        self.title_key = next((name for name in names if self.kinds[name] == "text"), names[0] if names else ROW_KEY)

    def fields(self) -> list[dict]:
        self._fresh()
        return [field(name, name, self.kinds[name], declared=declared, read_only=not self.is_editable or
                      self.kinds[name] == "json", primary_key=pk)
                for name, declared, _notnull, _default, pk in self.columns]

    def _execute(self, sql: str, params) -> list:
        try:
            return self.connection.execute(sql, params).fetchall()
        except sqlite3.Error as error:
            raise EngineError(f"{self.title}: {error}", code="database_error") from None

    def _rows(self, found: list, offset: int) -> list[dict]:
        names = [name for name, *_rest in self.columns]
        rows = []
        for index, values in enumerate(found):
            rowid, rest = (values[0], values[1:]) if self.is_editable else (offset + index + 1, values)
            row = {ROW_KEY: rowid}
            for name, value in zip(names, rest):
                row[name] = f"<{len(value)} bytes>" if isinstance(value, bytes) else \
                    bool(value) if self.kinds[name] == "checkbox" and value is not None else value
            rows.append(row)
        return rows

    def query(self, question: dict) -> tuple[list[dict], int, list[dict]]:
        self._fresh()
        columns = {name: quote_identifier(name) for name, *_rest in self.columns}
        if self.is_editable:
            columns[ROW_KEY] = "rowid"
        kinds = dict(self.kinds) | {ROW_KEY: "integer"}
        text_keys = [name for name in columns if kinds.get(name) in ("text", "long_text", "date", "datetime")]
        compiled = compile_query(question, columns, kinds, text_keys, "rowid" if self.is_editable else "")
        select = ", ".join((["rowid"] if self.is_editable else []) + [columns[name] for name, *_rest in self.columns])
        found, total = run_compiled(self._execute, self.table, ("", []), compiled, select, columns)
        fields = self.fields()
        if compiled.group:
            fields = [item for item in fields if item["key"] in compiled.group] + \
                [field(name, name.split("_")[0].capitalize(), "number") for name, _sql in compiled.aggregates]
            return found, total, fields
        rows = self._rows(found, compiled.offset)
        if question.get("fields"):
            keep = set(question["fields"]) | {ROW_KEY, self.title_key}
            rows = [{key: value for key, value in row.items() if key in keep} for row in rows]
            fields = [item for item in fields if item["key"] in keep]
        return rows, total, fields

    def get(self, row_id) -> dict:
        self._fresh()
        if not self.is_editable:
            raise self.refuse("rows of a view have no lasting id")
        found = self._execute(f"SELECT rowid, * FROM {self.table} WHERE rowid = ?", (int(row_id),))
        if not found:
            raise EngineError(f"{self.title}: that row no longer exists", code="not_found")
        return self._rows(found, 0)[0]

    def _values(self, values: dict) -> dict:
        self._fresh()
        if not self.is_editable:
            raise self.refuse("views are read-only", "edit the tables they read from")
        clean = {}
        for key, value in values.items():
            if key not in self.kinds:
                raise EngineError(f"{self.title}: there is no column “{key}”", code="invalid_value")
            kind = self.kinds[key]
            value = coerce(kind, value, key)
            clean[key] = int(value) if kind == "checkbox" and value is not None else value
        return clean

    def _write(self, sql: str, params) -> sqlite3.Cursor:
        try:
            with self.connection:
                return self.connection.execute(sql, params)
        except sqlite3.IntegrityError as error:
            raise EngineError(f"{self.title}: {error}", code="invalid_value") from None
        except sqlite3.Error as error:
            raise EngineError(f"{self.title}: {error}", code="database_error") from None

    def create(self, title: str | None, values: dict) -> dict:
        clean = self._values({**({self.title_key: title} if title and self.title_key in self.kinds else {}), **values})
        if clean:
            names = ", ".join(quote_identifier(key) for key in clean)
            cursor = self._write(f"INSERT INTO {self.table} ({names}) VALUES ({', '.join('?' for _ in clean)})",
                                 list(clean.values()))
        else:
            cursor = self._write(f"INSERT INTO {self.table} DEFAULT VALUES", ())
        return self.get(cursor.lastrowid)

    def update(self, row_id, values: dict) -> tuple[dict, dict]:
        before = self.get(row_id)
        clean = self._values(values)
        if clean:
            assignments = ", ".join(f"{quote_identifier(key)} = ?" for key in clean)
            self._write(f"UPDATE {self.table} SET {assignments} WHERE rowid = ?", list(clean.values()) + [int(row_id)])
        after = self.get(row_id)
        return after, diff(before, after)

    def duplicate(self, row_id) -> dict:
        self.get(row_id)
        copied = [quote_identifier(name) for name, declared, _nn, _default, pk in self.columns
                  if not (pk and "INT" in declared.upper())]            # an INTEGER PRIMARY KEY gets a new value
        names = ", ".join(copied)
        cursor = self._write(f"INSERT INTO {self.table} ({names}) SELECT {names} FROM {self.table} WHERE rowid = ?",
                             (int(row_id),))
        return self.get(cursor.lastrowid)

    def delete(self, row_ids: list) -> list[str]:
        if not self.is_editable:
            raise self.refuse("views are read-only")
        ids = [int(item) for item in row_ids]
        marks = ", ".join("?" for _ in ids)
        names = [str(row[0]) for row in self._execute(
            f"SELECT {quote_identifier(self.title_key) if self.title_key != ROW_KEY else 'rowid'} FROM {self.table} "
            f"WHERE rowid IN ({marks})", ids)]
        self._write(f"DELETE FROM {self.table} WHERE rowid IN ({marks})", ids)
        return names

    def structure(self) -> dict:
        self._fresh()
        indexes = []
        for _seq, name, unique, *_rest in self._execute(f"PRAGMA index_list({self.table})", ()):
            columns = [info[2] for info in self._execute(f"PRAGMA index_info({quote_identifier(name)})", ())]
            indexes.append({"name": name, "unique": bool(unique), "columns": columns})
        foreign = [{"column": row[3], "table": row[2], "to": row[4]}
                   for row in self._execute(f"PRAGMA foreign_key_list({self.table})", ())]
        sql = self._execute("SELECT sql FROM sqlite_master WHERE name = ?", (self.ref.table,))
        columns = super().structure()["columns"]
        for column, (_name, _declared, notnull, default, _pk) in zip(columns, self.columns):
            column.update(not_null=notnull, default=default)
        return {"kind": "view" if self.is_view else "table", "title": self.title, "columns": columns,
                "indexes": indexes, "foreign_keys": foreign, "sql": sql[0][0] if sql else "",
                "rows": self._execute(f"SELECT COUNT(*) FROM {self.table}", ())[0][0]}

    def sql(self, script: str, row_limit: int) -> tuple[list[dict], bool]:
        """Run one or more statements on this table's database: (results, did it write). Stops at the first
        error, which comes back as that result's "error"."""
        results, wrote, statement = [], False, ""
        for line in script.splitlines(keepends=True):
            statement += line
            if not sqlite3.complete_statement(statement):
                continue
            results.append(self._statement(statement.strip(), row_limit))
            wrote = wrote or results[-1]["changed"] > 0 or results[-1]["writes"]
            statement = ""
            if results[-1]["error"]:
                return results, wrote
        if statement.strip():
            results.append(self._statement(statement.strip(), row_limit))
            wrote = wrote or results[-1]["changed"] > 0 or results[-1]["writes"]
        return results, wrote

    def _statement(self, statement: str, row_limit: int) -> dict:
        result = {"statement": statement, "columns": [], "rows": [], "truncated": False, "changed": 0, "error": "",
                  "writes": not statement.lstrip("(").split(None, 1)[0].upper() in ("SELECT", "WITH", "PRAGMA", "EXPLAIN")}
        try:
            with self.connection:
                cursor = self.connection.execute(statement)
                if cursor.description:
                    result["columns"] = [item[0] for item in cursor.description]
                    rows = cursor.fetchmany(row_limit + 1)
                    result["truncated"] = len(rows) > row_limit
                    result["rows"] = [[f"<{len(value)} bytes>" if isinstance(value, bytes) else value for value in row]
                                      for row in rows[:row_limit]]
                result["changed"] = max(cursor.rowcount, 0) if result["writes"] else 0
        except sqlite3.Error as error:
            result["error"] = str(error)
        return result

    def add_property(self, title: str, kind: str, options: tuple, formula: str) -> dict:
        if not self.is_editable:
            raise self.refuse("views are read-only")
        if kind not in SQL_TYPES:
            raise self.refuse(f"a database column can't be of kind “{kind}”")
        name = title.strip()
        if not name or name in self.kinds:
            raise EngineError(f"{self.title}: a new column needs a new name", code="invalid_value")
        self._write(f"ALTER TABLE {self.table} ADD COLUMN {quote_identifier(name)} {SQL_TYPES[kind]}", ())
        self._fresh()
        return field(name, name, self.kinds[name])
# --- END [1.2] ---
