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
# USAGE:
#   python tools/check_standard.py            all checks, exit code 1 if any error
#   python tools/check_standard.py --changed  only files changed since the last commit
#   python tools/check_standard.py --since 2026-11-15  earliest NOTES date accepted on changed files
# CONTRACT:
#   - Requires: Python 3.10+, git (for --changed and the dated-notes check). No DevOS imports.
#   - Exposes: command-line tool; check_file(path, text) -> list[str] for reuse in tests.
# NOTES:
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
SOURCE_GLOBS = ("app/**/*.py", "tools/**/*.py", "core/src/*.cpp", "core/src/*.hpp")
HEADER_KEYS = ("FILE:", "OVERVIEW:", "PURPOSE:", "CONTRACT:", "NOTES:")
NOTE_ENTRY = re.compile(r"^(#|//)\s+- \[(\d{4}-\d{2}-\d{2})\]")
SECTION = re.compile(r"^# \[(\d+\.\d+)\] (\w+)\s*$")
SECTION_END = re.compile(r"^# --- END \[(\d+\.\d+)\] ---\s*$")
INDEX_ENTRY = re.compile(r"^#\s{4}(\d+\.\d+) (\w+)")
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
    files = sorted({path for pattern in SOURCE_GLOBS for path in ROOT.glob(pattern)})
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
    for selector in unused_theme_selectors():
        print(f"warning app/ui/theme.py: #{selector} is styled but no widget uses that objectName")
    print(f"\n{len(files)} files checked, {error_count} error(s).")
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
# --- END [2.1] ---
