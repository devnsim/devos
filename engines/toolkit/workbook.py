# ==============================================================================
# FILE: engines/toolkit/workbook.py
# OVERVIEW: Spreadsheet data and files: workbooks of sheets of cells (raw text or formulas), computed
#           values with dependency-safe evaluation, and reading / writing .xlsx, .csv and (through
#           LibreOffice headless) .ods / .xls / .pdf.
# PURPOSE:
#   1. Sheet: cells {(row, col): raw text}; value() computes formulas (cached, cycles become #CYCLE!).
#   2. Workbook: named sheets; load(path) / save(path) by extension.
#   3. LibreOffice: convert_with_libreoffice(source, extension) for formats openpyxl doesn't speak.
# CONTRACT:
#   - Requires: engines.toolkit.formulas, openpyxl (optional: .xlsx), soffice on PATH (optional:
#               .ods / .xls / .pdf). No Qt.
#   - Exposes: Sheet, Workbook, SpreadsheetError, parse_literal(text), format_value(value),
#              libreoffice_path(), convert_with_libreoffice(source, extension, out_dir), has_openpyxl(),
#              NATIVE_EXTENSIONS, OFFICE_EXTENSIONS
# NOTES:
#   - [2026-11-17] Moved from app/modules/spreadsheet/engine.py (Phase 5a: the Data engine reads workbooks too).
#   - [2026-11-16] Created with the Spreadsheet module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - raw: What the user typed into a cell ("12", "hello", "=A1*2"); formulas start with "=".
# - value: What a cell shows: a number, text, bool, None (empty) or an error code string ("#DIV/0!").
# - NATIVE_EXTENSIONS: read / written in Python (.xlsx needs openpyxl). OFFICE_EXTENSIONS: via LibreOffice.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CELLS
#    1.1 imports_and_values
#    1.2 sheet
# 2. FILES
#    2.1 workbook
#    2.2 libreoffice
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_values
# ==============================================================================
from __future__ import annotations

import csv
import datetime as dt
import shutil
import subprocess
import tempfile
from pathlib import Path

from engines.toolkit.formulas import FormulaError, column_name, evaluate, parse

NATIVE_EXTENSIONS = (".xlsx", ".xlsm", ".csv", ".tsv")
OFFICE_EXTENSIONS = (".ods", ".xls", ".fods")


class SpreadsheetError(Exception):
    pass


def has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def parse_literal(text: str):
    """A non-formula cell's value: number if it reads as one, else the text (None if empty)."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.upper() in ("TRUE", "FALSE"):
        return stripped.upper() == "TRUE"
    try:
        return float(stripped.replace(",", "")) if stripped[0] in "+-.0123456789" else text
    except ValueError:
        return text


def format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)
# --- END [1.1] ---

# ==============================================================================
# [1.2] sheet
# ==============================================================================
class Sheet:
    def __init__(self, name: str = "Sheet1") -> None:
        self.name = name
        self.cells: dict[tuple[int, int], str] = {}
        self._cache: dict[tuple[int, int], object] = {}
        self._evaluating: set[tuple[int, int]] = set()

    def raw(self, row: int, col: int) -> str:
        return self.cells.get((row, col), "")

    def set_raw(self, row: int, col: int, text: str) -> None:
        if text == "":
            self.cells.pop((row, col), None)
        else:
            self.cells[(row, col)] = text
        self._cache.clear()                      # any formula may depend on this cell

    def used_size(self) -> tuple[int, int]:
        """(rows, cols) spanned by non-empty cells."""
        if not self.cells:
            return 0, 0
        return max(row for row, _ in self.cells) + 1, max(col for _, col in self.cells) + 1

    def value(self, row: int, col: int):
        """Computed value; formula errors come back as their code (e.g. "#DIV/0!")."""
        key = (row, col)
        if key in self._cache:
            return self._cache[key]
        raw = self.cells.get(key, "")
        if not raw.startswith("=") or len(raw) == 1:
            result = parse_literal(raw)
        else:
            try:
                result = self._evaluate(key, raw)
            except FormulaError as error:
                result = error.code
            except RecursionError:
                result = "#CYCLE!"
        self._cache[key] = result
        return result

    def error_detail(self, row: int, col: int) -> str:
        """Why a formula cell failed (for its tooltip)."""
        raw = self.raw(row, col)
        if not raw.startswith("="):
            return ""
        try:
            evaluate(parse(raw), _Resolver(self, set()))
            return ""
        except FormulaError as error:
            return str(error)

    def _evaluate(self, key: tuple[int, int], raw: str):
        if key in self._evaluating:
            raise FormulaError("#CYCLE!", "the formula refers to itself")
        self._evaluating.add(key)
        try:
            return evaluate(parse(raw), _Resolver(self, self._evaluating))
        finally:
            self._evaluating.discard(key)

    def column_values(self, col: int, first_row: int, last_row: int) -> list:
        return [self.value(row, col) for row in range(first_row, last_row + 1)]


class _Resolver:
    def __init__(self, sheet: Sheet, evaluating: set) -> None:
        self.sheet, self.evaluating = sheet, evaluating

    def cell(self, row: int, col: int):
        if (row, col) in self.evaluating:
            raise FormulaError("#CYCLE!", f"{column_name(col)}{row + 1} depends on itself")
        value = self.sheet.value(row, col)
        if isinstance(value, str) and value.startswith("#") and value.endswith(("!", "?")):
            raise FormulaError(value, f"{column_name(col)}{row + 1} is {value}")
        return value

    def cells(self, row1: int, col1: int, row2: int, col2: int) -> list:
        if (row2 - row1 + 1) * (col2 - col1 + 1) > 200_000:
            raise FormulaError("#REF!", "range too large")
        return [self.cell(row, col) for row in range(row1, row2 + 1) for col in range(col1, col2 + 1)]
# --- END [1.2] ---


# ==============================================================================
# [2.1] workbook
# ==============================================================================
class Workbook:
    def __init__(self, sheets: list[Sheet] | None = None) -> None:
        self.sheets = sheets or [Sheet("Sheet1")]

    def sheet_named(self, name: str) -> Sheet | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)

    def add_sheet(self, name: str | None = None) -> Sheet:
        number = len(self.sheets) + 1
        while name is None or self.sheet_named(name):
            name, number = f"Sheet{number}", number + 1
        sheet = Sheet(name)
        self.sheets.append(sheet)
        return sheet

    @staticmethod
    def load(path: str | Path) -> "Workbook":
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in OFFICE_EXTENSIONS:
            with tempfile.TemporaryDirectory() as folder:
                return Workbook.load(convert_with_libreoffice(path, ".xlsx", Path(folder)))
        if suffix in (".csv", ".tsv"):
            sheet = Sheet(path.stem[:31] or "Sheet1")
            with path.open(newline="", encoding="utf-8-sig") as handle:
                for row, values in enumerate(csv.reader(handle, delimiter="\t" if suffix == ".tsv" else ",")):
                    for col, text in enumerate(values):
                        if text != "":
                            sheet.cells[(row, col)] = text
            return Workbook([sheet])
        if suffix in (".xlsx", ".xlsm"):
            if not has_openpyxl():
                raise SpreadsheetError("reading .xlsx needs openpyxl (in shell.nix)")
            import openpyxl
            try:
                book = openpyxl.load_workbook(path, data_only=False)
            except Exception as error:                  # openpyxl raises many types for bad files
                raise SpreadsheetError(f"not a readable workbook: {error}") from error
            sheets = []
            for worksheet in book.worksheets:
                sheet = Sheet(worksheet.title)
                for cells in worksheet.iter_rows():
                    for cell in cells:
                        if cell.value is None:
                            continue
                        value = cell.value
                        if isinstance(value, (dt.date, dt.datetime, dt.time)):
                            text = value.isoformat()
                        elif isinstance(value, bool):
                            text = "TRUE" if value else "FALSE"
                        elif isinstance(value, float) and value.is_integer():
                            text = str(int(value))
                        else:
                            text = str(value)
                        sheet.cells[(cell.row - 1, cell.column - 1)] = text
                sheets.append(sheet)
            return Workbook(sheets or [Sheet()])
        raise SpreadsheetError(f"can't open {suffix or 'files without an extension'}")

    def save(self, path: str | Path) -> None:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".csv", ".tsv"):
            sheet = self.sheets[0]
            rows, cols = sheet.used_size()
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t" if suffix == ".tsv" else ",")
                for row in range(rows):
                    writer.writerow([sheet.raw(row, col) for col in range(cols)])
            return
        if suffix in (".xlsx", ".xlsm"):
            if not has_openpyxl():
                raise SpreadsheetError("writing .xlsx needs openpyxl (in shell.nix)")
            import openpyxl
            book = openpyxl.Workbook()
            book.remove(book.active)
            for sheet in self.sheets:
                worksheet = book.create_sheet(sheet.name[:31])
                for (row, col), raw in sheet.cells.items():
                    value = raw if raw.startswith("=") else parse_literal(raw)
                    worksheet.cell(row=row + 1, column=col + 1, value=value)
            book.save(path)
            return
        if suffix in OFFICE_EXTENSIONS + (".pdf",):
            with tempfile.TemporaryDirectory() as folder:
                staging = Path(folder) / f"{path.stem}.xlsx"
                self.save(staging)
                converted = convert_with_libreoffice(staging, suffix, Path(folder))
                shutil.move(str(converted), path)
            return
        raise SpreadsheetError(f"can't save as {suffix}")
# --- END [2.1] ---

# ==============================================================================
# [2.2] libreoffice
# ==============================================================================
def libreoffice_path() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_with_libreoffice(source: Path, extension: str, out_dir: Path) -> Path:
    """Convert a file with LibreOffice running headless (its own throw-away profile, so an open
    LibreOffice window doesn't block it). Returns the converted file."""
    program = libreoffice_path()
    if program is None:
        raise SpreadsheetError("LibreOffice (soffice) isn't installed, so this format can't be converted")
    target_format = extension.lstrip(".")
    with tempfile.TemporaryDirectory() as profile:
        command = [program, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", target_format,
                   "--outdir", str(out_dir), str(source)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as error:
            raise SpreadsheetError("LibreOffice took too long converting the file") from error
    converted = out_dir / f"{source.stem}.{target_format}"
    if result.returncode != 0 or not converted.exists():
        raise SpreadsheetError(f"LibreOffice couldn't convert the file: {(result.stderr or result.stdout).strip()[:300]}")
    return converted
# --- END [2.2] ---
