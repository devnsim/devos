# ==============================================================================
# FILE: tools/check_standard.py
# OVERVIEW: Checks every source file against the DevOS code standard and reports what to fix.
# PURPOSE:
#   1. Header: FILE (matching the real path), OVERVIEW, PURPOSE, CONTRACT, NOTES with dated entries.
#   2. [DEFINITIONS] and [INDEX] blocks present; INDEX entries match the numbered section markers;
#      every "# [X.Y] name" section has its "# --- END [X.Y] ---".
#   3. Changed files (git: modified or new since HEAD) carry a NOTES entry dated on or after the last
#      commit (sessions that cross midnight still pass; untouched old notes don't).
#   4. Theme: every #selector in app/ui/theme.py is used by some widget objectName.
#   5. Layer boundaries: engines/ and app/data/ never import Qt, views or modules (BOUNDARIES); an engine package's
#      views.py / views/ may use Qt but not modules; engines never import each other (only kernel / toolkit); app/
#      never imports an engine package (only engines.kernel / runtime / toolkit) - e.g. only Knowledge touches the
#      Library's store.
#   6. ROADMAP.md: a warning when files changed but the roadmap wasn't updated.
# USAGE:
#   python tools/check_standard.py            all checks, exit code 1 if any error
#   python tools/check_standard.py --changed  only files changed since the last commit
#   python tools/check_standard.py --since 2026-11-15  earliest NOTES date accepted on changed files
# CONTRACT:
#   - Requires: Python 3.10+, git (for --changed and the dated-notes check). No DevOS imports.
#   - Exposes: command-line tool; check_file(path, text) -> (errors, warnings) and boundary_errors(relative, lines)
#              for reuse in tests.
# NOTES:
#   - [2026-11-17] app/ never imports an engine package's internals (Phase 4: only Knowledge touches the Library).
#   - [2026-11-17] Engines never import each other; an engine package's views (views.py / views/) may use Qt.
#   - [2026-11-17] Warns when files changed but ROADMAP.md didn't (the roadmap is updated every session).
#   - [2026-11-17] Layer boundaries (engines/, app/data/ import no Qt, views or modules); engines/**/*.py checked.
#   - [2026-11-16] Checks the physics engine's C++ (core/physics/**), skipping build/ and fetched _deps/.
#   - [2026-11-16] Dated-notes rule: a changed file needs a NOTES entry dated on/after the last commit's
#                  date (was: exactly today, which failed every file of a session that crossed midnight).
#   - [2026-11-15] Created in the health pass so humans and agents can verify the standard in one
#                  command instead of by eye. Empty __init__.py files are skipped.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - error: A standard violation (fails the run). warning: worth a look, doesn't fail.
# - section marker: "# [X.Y] snake_name" line; its INDEX entry is "#    X.Y snake_name".
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHECKS
#    1.1 imports_and_patterns
#    1.2 check_file
#    1.3 theme_selectors
# 2. CLI
#    2.1 main
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_patterns
# ==============================================================================
import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_GLOBS = ("app/**/*.py", "engines/**/*.py", "examples/**/*.py", "tools/**/*.py", "core/src/*.cpp", "core/src/*.hpp",
                "core/physics/**/*.cpp", "core/physics/**/*.hpp")
SKIPPED_PARTS = {"build", "_deps"}            # build output and fetched third-party code (Jolt)
HEADER_KEYS = ("FILE:", "OVERVIEW:", "PURPOSE:", "CONTRACT:", "NOTES:")
NOTE_ENTRY = re.compile(r"^(#|//)\s+- \[(\d{4}-\d{2}-\d{2})\]")
SECTION = re.compile(r"^# \[(\d+\.\d+)\] (\w+)\s*$")
SECTION_END = re.compile(r"^# --- END \[(\d+\.\d+)\] ---\s*$")
INDEX_ENTRY = re.compile(r"^#\s{4}(\d+\.\d+) (\w+)")
IMPORT_LINE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")
# (folder, imports it may never make, why) - checked on every import line, lazy imports included
BOUNDARIES = (
    ("engines/", ("PySide6", "app.ui", "app.modules", "app.views"),
     "engines run without a UI: no Qt, no views, no modules (docs/architecture/engine-plan.md)"),
    ("app/data/", ("PySide6", "app.ui", "app.modules", "app.views"),
     "the data layer has no Qt and knows no UI or module"),
    ("examples/engines/", ("PySide6", "app.ui", "app.modules", "app.views"),
     "engines run without a UI: no Qt, no views, no modules (docs/architecture/engine-plan.md)"),
)
SHARED_ENGINE_PACKAGES = ("kernel", "toolkit", "runtime")       # what every engine may import
# --- END [1.1] ---


# ==============================================================================
# [1.2] check_file
# ==============================================================================
def check_file(path: Path, text: str, min_note_date: str | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one file."""
    errors, warnings = [], []
    relative = path.relative_to(ROOT).as_posix()
    lines = text.splitlines()
    header = "\n".join(lines[:60])
    for key in HEADER_KEYS:
        if key not in header:
            errors.append(f"header: missing {key}")
    file_line = next((line for line in lines[:5] if "FILE:" in line), "")
    if file_line and relative not in file_line:
        errors.append(f"header: FILE line says '{file_line.split('FILE:')[-1].strip()}', file is '{relative}'")
    note_dates = [match.group(2) for line in lines[:120] if (match := NOTE_ENTRY.match(line))]
    if "NOTES:" in header and not note_dates:
        errors.append("header: NOTES has no dated '- [YYYY-MM-DD]' entry")
    if min_note_date and note_dates and max(note_dates) < min_note_date:
        errors.append(f"notes: changed since the last commit but no NOTES entry dated {min_note_date} or later")

    if path.suffix != ".py":
        return errors, warnings                       # C++: header + notes only
    errors += boundary_errors(relative, lines)
    if "# [DEFINITIONS]" not in text:
        errors.append("missing [DEFINITIONS] block")
    if "# [INDEX]" not in text:
        errors.append("missing [INDEX] block")
        return errors, warnings

    index_start = lines.index("# [INDEX]")
    index_entries = []
    for line in lines[index_start + 1:]:
        if line.startswith("# ====="):
            break
        match = INDEX_ENTRY.match(line)
        if match:
            index_entries.append((match.group(1), match.group(2)))
    sections = [(m.group(1), m.group(2)) for line in lines if (m := SECTION.match(line))]
    ends = {m.group(1) for line in lines if (m := SECTION_END.match(line))}
    if index_entries != sections:
        missing = [f"{n} {s}" for n, s in sections if (n, s) not in index_entries]
        stale = [f"{n} {s}" for n, s in index_entries if (n, s) not in sections]
        if missing or stale:
            errors.append(f"[INDEX] out of step: not listed {missing or '-'}; listed but absent {stale or '-'}")
        else:
            warnings.append("[INDEX] lists the sections in a different order than the file")
    for number, name in sections:
        if number not in ends:
            errors.append(f"section [{number}] {name} has no '# --- END [{number}] ---'")
    return errors, warnings


def _engine_package(relative: str) -> tuple[str | None, list[str]]:
    """(package name, path parts inside it) for files of an engine package (engines/<p>/, examples/engines/<p>/)."""
    parts = relative.split("/")
    if parts[0] == "engines" and len(parts) > 2:
        return parts[1], parts[2:]
    if parts[:2] == ["examples", "engines"] and len(parts) > 3:
        return parts[2], parts[3:]
    return None, []


def _is_engine_views(relative: str) -> bool:
    """An engine package's front-end part (views.py / views/): it may use Qt and app.views, never app.modules."""
    _package, inside = _engine_package(relative)
    return bool(inside) and ("views" in inside[:-1] or inside[-1] == "views.py")


def boundary_errors(relative: str, lines: list[str]) -> list[str]:
    """Imports that cross a layer boundary (BOUNDARIES), and engines importing each other."""
    errors = []
    own, _inside = _engine_package(relative)
    for folder, forbidden, why in BOUNDARIES:
        if not relative.startswith(folder):
            continue
        if folder in ("engines/", "examples/engines/") and _is_engine_views(relative):
            forbidden, why = ("app.modules",), "an engine's views talk to the host, never to modules"
        for number, line in enumerate(lines, start=1):
            match = IMPORT_LINE.match(line)
            module = match and (match.group(1) or match.group(2))
            if module and any(module == name or module.startswith(name + ".") for name in forbidden):
                errors.append(f"line {number}: imports {module} - {why}")
    if relative.startswith("app/"):                   # the front end talks to engines over the bus, never inside them
        for number, line in enumerate(lines, start=1):
            match = IMPORT_LINE.match(line)
            module = (match and (match.group(1) or match.group(2))) or ""
            package = module.split(".")[1] if module.startswith("engines.") else None
            if package and package not in SHARED_ENGINE_PACKAGES + ("kernel",):
                errors.append(f"line {number}: imports {module} - ask the engine over the bus "
                              f"(the Library: app.clients.library)")
    if own and own not in SHARED_ENGINE_PACKAGES:
        for number, line in enumerate(lines, start=1):
            match = IMPORT_LINE.match(line)
            module = (match and (match.group(1) or match.group(2))) or ""
            other = module.split(".")[1] if module.startswith("engines.") and module.count(".") >= 1 else None
            if other and other != own and other not in SHARED_ENGINE_PACKAGES:
                errors.append(f"line {number}: imports {module} - engines never import each other (ask over the bus)")
    return errors
# --- END [1.2] ---

# ==============================================================================
# [1.3] theme_selectors
# ==============================================================================
def unused_theme_selectors() -> list[str]:
    theme = (ROOT / "app/ui/theme.py").read_text()
    qss = theme[theme.find('BASE_QSS = f"""'):]
    code = "\n".join(path.read_text() for path in (ROOT / "app").rglob("*.py") if path.name != "theme.py")
    selectors = sorted(set(re.findall(r"#([a-z][a-z0-9_]+)", qss)))
    selectors = [name for name in selectors if not re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}", name)]   # hex colours
    return [name for name in selectors if not re.search(r"['\"]%s['\"]" % name, code)]


def roadmap_is_stale(changed: set[str]) -> bool:
    """Work changed files but ROADMAP.md wasn't touched: the roadmap is updated every session (AGENTS.md)."""
    return bool(changed - {"ROADMAP.md"}) and "ROADMAP.md" not in changed
# --- END [1.3] ---


# ==============================================================================
# [2.1] main
# ==============================================================================
def changed_files() -> set[str]:
    try:
        output = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line[3:].strip() for line in output.splitlines() if not line.startswith(" D")}


def last_commit_date() -> str:
    """Date of HEAD (YYYY-MM-DD); today when there is no commit or no git."""
    try:
        output = subprocess.run(["git", "log", "-1", "--format=%cs"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        output = ""
    return output or dt.date.today().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the DevOS code standard")
    parser.add_argument("--changed", action="store_true", help="only files changed since the last commit")
    parser.add_argument("--since", default=last_commit_date(), help="earliest NOTES date accepted on changed files")
    arguments = parser.parse_args()

    changed = changed_files()
    files = sorted({path for pattern in SOURCE_GLOBS for path in ROOT.glob(pattern)
                    if not SKIPPED_PARTS & set(path.relative_to(ROOT).parts)})
    error_count = 0
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text()
        if path.name == "__init__.py" and len(text.strip().splitlines()) <= 1:
            continue
        is_changed = relative in changed
        if arguments.changed and not is_changed:
            continue
        errors, warnings = check_file(path, text, arguments.since if is_changed else None)
        for message in errors:
            print(f"ERROR   {relative}: {message}")
        for message in warnings:
            print(f"warning {relative}: {message}")
        error_count += len(errors)
    if roadmap_is_stale(changed):
        print("warning ROADMAP.md: files changed but the roadmap wasn't updated (Now / Next / Done, Last updated)")
    for selector in unused_theme_selectors():
        print(f"warning app/ui/theme.py: #{selector} is styled but no widget uses that objectName")
    print(f"\n{len(files)} files checked, {error_count} error(s).")
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
# --- END [2.1] ---
