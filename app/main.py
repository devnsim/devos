# ==============================================================================
# FILE: app/main.py
# OVERVIEW: Entry point that starts the DevOS application.
# PURPOSE:
#   1. Creates the Qt application and gives it a name and an icon.
#   2. Links the app to its desktop launcher so the taskbar shows the right icon.
#   3. Applies the global theme (colors and styles) from theme.py.
#   4. Opens the main window and keeps the app running until it is closed.
#   Run from the project root with:  python -m app.main
# CONTRACT:
#   - Requires: app.ui.main_window.MainWindow, app.ui.theme.STYLESHEET,
#               assets/devos.svg, devos.desktop (created by the install script)
#   - Exposes: main() -> int (process exit code)
# NOTES:
#   - [2026-11-14] Reformatted to the file standard; ICON renamed to APP_ICON_PATH,
#                  window renamed to main_window, magic strings moved to constants.
#   - [2026-11-14] Added window icon and setDesktopFileName for the Wayland taskbar icon.
#   - [2026-11-14] main() prints a warning if APP_ICON_PATH is missing instead of failing silently.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - APP_ICON_PATH (Path): Absolute path to assets/devos.svg, found relative to this file.
# - APP_NAME (str): Application display name shown by the system (default: "DevOS").
# - DESKTOP_FILE_ID (str): .desktop launcher name without extension; must match the
#   file the install script creates (default: "devos").
# - exit_code (int): Value returned by app.exec() when the window closes (0 = normal exit).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 app_constants
# 2. APPLICATION_STARTUP
#    2.1 main
# 3. SCRIPT_ENTRY
#    3.1 run_as_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.theme import STYLESHEET
# --- END [1.1] ---

# ==============================================================================
# [1.2] app_constants
# ==============================================================================
APP_NAME = "DevOS"
DESKTOP_FILE_ID = "devos"
APP_ICON_PATH = Path(__file__).parent.parent / "assets" / "devos.svg"
# --- END [1.2] ---

# ==============================================================================
# [2.1] main
# ==============================================================================
def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if not APP_ICON_PATH.exists():
        print(f"[DevOS] app icon not found: {APP_ICON_PATH}")
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setDesktopFileName(DESKTOP_FILE_ID)
    # Note:
    # - This setDesktopFileName connects the window to devos.desktop on Wayland
    # - On Plasma’s Wayland session, the setDesktopFileName line is what makes the taskbar show your icon instead of a generic one.

    app.setStyleSheet(STYLESHEET)

    main_window = MainWindow()
    main_window.show()

    exit_code = app.exec()
    return exit_code
# --- END [2.1] ---

# ==============================================================================
# [3.1] run_as_module
# ==============================================================================
if __name__ == "__main__":
    sys.exit(main())
# --- END [3.1] ---
