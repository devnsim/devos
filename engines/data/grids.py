# ==============================================================================
# FILE: engines/data/grids.py
# OVERVIEW: Grids as Data collections: CSV datasets ("csv:<item id>") and workbook sheets
#           ("sheet:<item id>/<sheet>"). The first row names the columns; every row below is a row. A column
#           made of formulas is a computed field (read-only, the spreadsheet computes it).
# PURPOSE:
#   1. GridTable: fields (kinds inferred from the values), query (in memory, by the table toolkit), get, and
#      writes - update (not formula cells), create (a new last row), duplicate and delete (only in sheets
#      without formulas: rows would shift under them).
#   2. Writes keep the file's own format: .xlsx is edited in place with openpyxl (formatting stays); CSV / TSV
#      are rewritten. Formats DevOS reads through LibreOffice (.ods, .xls) are read-only here.
#   3. sheet_names(path): the sheets of a workbook.
# CONTRACT:
#   - Requires: engines.toolkit (workbook, formulas, table), engines.data.common, openpyxl (for .xlsx).
#   - Exposes: GridTable(ref, path, title), sheet_names(path), EDITABLE_EXTENSIONS
# NOTES:
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - row number: A row's key ("_rowid") is its row number in the file (2 = the first row under the header).
# - EDITABLE_EXTENSIONS (tuple): File types Data writes (".csv", ".tsv", ".xlsx", ".xlsm").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. GRIDS
#    1.1 reading
#    1.2 grid_table
#    1.3 writing
# ==============================================================================

# ==============================================================================
# [1.1] reading
# ==============================================================================
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from engines.data.common import ROW_KEY, FileRef, Source, coerce, diff, field, infer_kind
from engines.kernel import EngineError
from engines.toolkit.formulas import column_name
from engines.toolkit.table import run_query
from engines.toolkit.workbook import SpreadsheetError, Workbook, format_value

EDITABLE_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".xlsm")


def load_workbook(path: Path) -> Workbook:
    try:
        return Workbook.load(path)
    except (SpreadsheetError, OSError, UnicodeDecodeError) as error:
        raise EngineError(f"{path.name} can't be read: {error}", code="unreadable") from None


def sheet_names(path: Path) -> list[str]:
    return [sheet.name for sheet in load_workbook(path).sheets]
# --- END [1.1] ---


# ==============================================================================
# [1.2] grid_table
# ==============================================================================
class GridTable(Source):
    def __init__(self, ref: FileRef, path: Path, title: str) -> None:
        super().__init__(ref.collection)
        self.ref, self.path, self.title = ref, path, title
        self._stamp = None
        self._load()

    def _load(self) -> None:
        stat = self.path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return
        book = load_workbook(self.path)
        sheet = book.sheets[0] if self.ref.prefix == "csv" else book.sheet_named(self.ref.table)
        if sheet is None:
            raise EngineError(f"{self.title}: there is no sheet “{self.ref.table}”", code="not_found")
        self.sheet, self._stamp = sheet, stamp
        rows, cols = sheet.used_size()
        self.row_count, self.col_count = rows, cols
        keys, self.columns = set(), []
        for col in range(cols):
            name = format_value(sheet.value(0, col)).strip() or column_name(col)
            key, number = name, 2
            while key in keys or key == ROW_KEY:
                key, number = f"{name} {number}", number + 1
            keys.add(key)
            self.columns.append(key)
        self.rows = []
        for row in range(1, rows):
            if any(sheet.raw(row, col) for col in range(cols)):
                self.rows.append({ROW_KEY: row + 1, **{key: _plain(sheet.value(row, col)) for col, key in enumerate(self.columns)}})
        self.formula_columns = {}
        for col, key in enumerate(self.columns):
            raws = [sheet.raw(row, col) for row in range(1, rows) if sheet.raw(row, col)]
            if raws and all(raw.startswith("=") for raw in raws):
                self.formula_columns[key] = raws[0]
        self.has_formulas = any(raw.startswith("=") for raw in sheet.cells.values())
        self.kinds = {key: infer_kind([row[key] for row in self.rows if not _is_error(row[key])]) for key in self.columns}
        self.title_key = next((key for key in self.columns if self.kinds[key] == "text"),
                              self.columns[0] if self.columns else ROW_KEY)

    @property
    def is_editable(self) -> bool:
        return self.path.suffix.lower() in EDITABLE_EXTENSIONS

    def fields(self) -> list[dict]:
        self._load()
        return [field(key, key, self.kinds[key], column=column_name(col),
                      **({"formula": self.formula_columns[key], "computed": True, "read_only": True}
                         if key in self.formula_columns else {"read_only": not self.is_editable}))
                for col, key in enumerate(self.columns)]

    def query(self, question: dict) -> tuple[list[dict], int, list[dict]]:
        fields = self.fields()
        return run_query([dict(row) for row in self.rows], fields, question, self.title_key, ROW_KEY)

    def get(self, row_id) -> dict:
        self._load()
        row = next((row for row in self.rows if row[ROW_KEY] == int(row_id)), None)
        if row is None:
            raise EngineError(f"{self.title}: row {row_id} is empty or gone", code="not_found")
        return dict(row)
# --- END [1.2] ---


# ==============================================================================
# [1.3] writing
# ==============================================================================
    def _check_editable(self) -> None:
        if not self.is_editable:
            raise self.refuse(f"{self.path.suffix} files are read-only here", "open it in the Spreadsheet")

    def _raws(self, values: dict, row: int | None) -> dict[int, str]:
        """{column index: raw text} for the edited values (formula cells refused)."""
        raws = {}
        for key, value in values.items():
            if key not in self.columns:
                raise EngineError(f"{self.title}: there is no column “{key}”", code="invalid_value")
            col = self.columns.index(key)
            if key in self.formula_columns or (row is not None and self.sheet.raw(row - 1, col).startswith("=")):
                raise self.refuse(f"“{key}” is computed by a formula", "change the formula in the Spreadsheet")
            value = coerce(self.kinds[key], value, key)
            raws[col] = "" if value is None else ("TRUE" if value else "FALSE") if isinstance(value, bool) else \
                format_value(float(value)) if isinstance(value, (int, float)) else str(value)
        return raws

    def _commit(self, cells: dict[tuple[int, int], str], delete_rows: list[int] | None = None) -> None:
        """Write cells ((row, col) 0-based -> raw) and / or delete rows (row numbers) to the file, then reload."""
        self._check_editable()
        suffix = self.path.suffix.lower()
        handle, temporary = tempfile.mkstemp(suffix=suffix, dir=self.path.parent)
        os.close(handle)
        try:
            if suffix in (".xlsx", ".xlsm"):
                import openpyxl
                book = openpyxl.load_workbook(self.path, keep_vba=suffix == ".xlsm")
                sheet = book[self.ref.table] if self.ref.prefix == "sheet" else book.worksheets[0]
                for (row, col), raw in cells.items():
                    sheet.cell(row=row + 1, column=col + 1,
                               value=None if raw == "" else raw if raw.startswith("=") else _literal(raw))
                for number in sorted(delete_rows or [], reverse=True):
                    sheet.delete_rows(number)
                book.save(temporary)
            else:
                book = load_workbook(self.path)
                grid = book.sheets[0]
                for (row, col), raw in cells.items():
                    grid.set_raw(row, col, raw)
                for number in sorted(delete_rows or [], reverse=True):
                    grid.cells = {(row if row < number - 1 else row - 1, col): raw
                                  for (row, col), raw in grid.cells.items() if row != number - 1}
                book.save(temporary)
            os.replace(temporary, self.path)
        except (OSError, SpreadsheetError, KeyError) as error:
            raise EngineError(f"{self.title}: couldn't save: {error}", code="io_error") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._stamp = None
        self._load()

    def update(self, row_id, values: dict) -> tuple[dict, dict]:
        self._check_editable()
        before = self.get(row_id)
        raws = self._raws(values, int(row_id))
        self._commit({(int(row_id) - 1, col): raw for col, raw in raws.items()})
        after = self.get(row_id)
        return after, diff(before, after)

    def create(self, title: str | None, values: dict) -> dict:
        self._check_editable()
        if title and self.title_key in self.columns:
            values = {self.title_key: title, **values}
        raws = self._raws(values, None) or {0: ""}
        row = max(self.row_count, 1)                    # 0-based index of the new last row
        if not any(raws.values()):
            raise EngineError(f"{self.title}: a new row needs at least one value", code="invalid_value")
        self._commit({(row, col): raw for col, raw in raws.items()})
        return self.get(row + 1)

    def _no_formulas(self, what: str) -> None:
        if self.has_formulas:
            raise self.refuse(f"rows can't be {what} here: formulas would point at the wrong rows",
                              "use the Spreadsheet")

    def duplicate(self, row_id) -> dict:
        self._check_editable()
        self._no_formulas("copied")
        source = self.get(row_id)
        row = self.row_count
        self._commit({(row, col): self.sheet.raw(int(source[ROW_KEY]) - 1, col) for col in range(self.col_count)})
        return self.get(row + 1)

    def delete(self, row_ids: list) -> list[str]:
        self._check_editable()
        self._no_formulas("deleted")
        rows = [self.get(row_id) for row_id in row_ids]
        self._commit({}, [int(row[ROW_KEY]) for row in rows])
        return [format_value(row.get(self.title_key)) for row in rows]


def _literal(raw: str):
    from engines.toolkit.workbook import parse_literal
    value = parse_literal(raw)
    return int(value) if isinstance(value, float) and value.is_integer() and abs(value) < 1e15 else value


def _plain(value):
    """Whole numbers as int (sheets compute in floats)."""
    return int(value) if isinstance(value, float) and value.is_integer() and abs(value) < 1e15 else value


def _is_error(value) -> bool:
    return isinstance(value, str) and value.startswith("#") and value.endswith(("!", "?"))
# --- END [1.3] ---
