# ==============================================================================
# FILE: app/main.py
# OVERVIEW: Entry point that starts the DevOS application.
# PURPOSE:
#   1. Creates the Qt application and gives it a name and an icon.
#   2. Links the app to its desktop launcher so the taskbar shows the right icon.
#   3. Applies the global theme (colors and styles) from theme.py.
#   4. Opens the main window and keeps the app running until it is closed.
#   5. Starts the activity log session: hooks for unhandled errors and Qt warnings, start-up steps,
#      database attached for saving, session ended (and flushed) on quit.
#   Run from the project root with:  python -m app.main
# CONTRACT:
#   - Requires: app.ui.main_window.MainWindow, app.ui.theme.STYLESHEET, app.data.activity_log, app.native,
#               assets/devos.svg, devos.desktop (created by the install script)
#   - Exposes: main() -> int (process exit code)
# NOTES:
#   - [2026-11-17] Engines (engines.runtime) are shut down at exit.
#   - [2026-11-17] Start-up applies a restore chosen in Settings, then holds the data folder's running lock.
#   - [2026-11-16] Shuts Qt WebEngine down in order after the event loop (no segfault on quit).
#   - [2026-11-16] prepare_web_engine() runs before QApplication so browser pages (Qt WebEngine) work.
#   - [2026-11-14] Reformatted to the file standard; ICON renamed to APP_ICON_PATH,
#                  window renamed to main_window, magic strings moved to constants.
#   - [2026-11-14] Added window icon and setDesktopFileName for the Wayland taskbar icon.
#   - [2026-11-14] main() prints a warning if APP_ICON_PATH is missing instead of failing silently.
#   - [2026-11-15] Activity log session: start-up announcements, error / Qt-warning hooks, end_session().
#                  App modules are imported after the log starts so their start-up is logged too.
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

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.data.activity_log import get_activity_log
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
def _qt_message_handler(message_type, context, message: str) -> None:
    """Qt's own warnings go to the activity log instead of disappearing in a terminal."""
    if message_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        level = "warning" if message_type == QtMsgType.QtWarningMsg else "error"
        getattr(get_activity_log(), level)("qt", message)


def _restore_and_lock_data(log) -> None:
    """A restore chosen in Settings runs now, while nothing holds the data open; then the data folder is
    marked as in use so a command-line restore can't replace it under a running DevOS."""
    from app.data.store.backup import BackupError, apply_pending_restore, hold_running_lock
    try:
        apply_pending_restore()
    except (BackupError, OSError) as error:
        log.error("backup", "Restoring the chosen backup failed; the state before it is in Settings → Data → Backups",
                  error)
    if not hold_running_lock():
        log.warning("devos", "Another DevOS seems to be using the same data folder")


def main() -> int:
    log = get_activity_log()
    log.install_hooks()                              # unhandled errors -> System Log
    qInstallMessageHandler(_qt_message_handler)
    log.info("devos", f"DevOS starting (Python {sys.version.split()[0]})")
    _restore_and_lock_data(log)                      # before anything opens the data folder

    from app.ui.web_support import prepare_web_engine, shutdown_web_engine
    prepare_web_engine()                             # before QApplication (browser pages)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    log.info("devos", f"Qt {__import__('PySide6').__version__} on the '{app.platformName()}' display platform")
    if not APP_ICON_PATH.exists():
        log.warning("devos", f"App icon not found: {APP_ICON_PATH}")
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setDesktopFileName(DESKTOP_FILE_ID)
    # Note:
    # - This setDesktopFileName connects the window to devos.desktop on Wayland
    # - On Plasma’s Wayland session, the setDesktopFileName line is what makes the taskbar show your icon instead of a generic one.

    from app.data.store.database import get_database       # after the log, so opening it is logged
    from app.ui.main_window import MainWindow
    from app.ui.theme import STYLESHEET
    from app import native
    log.attach_database(get_database())
    if native.HAS_NATIVE:
        log.success("engine", f"Native engine loaded: {native.ENGINE_NAME}")
    else:
        log.warning("engine", "Native C++ engine not built; using the NumPy fallback (slower). Run: python -m app.native.build")

    app.setStyleSheet(STYLESHEET)
    with log.step("devos", "Build main window"):
        main_window = MainWindow()
    main_window.show()
    log.success("devos", "Ready")

    exit_code = app.exec()
    main_window.close()
    from engines.runtime import shutdown_engines
    shutdown_engines()                     # engines close their stores on their own threads
    shutdown_web_engine()                  # pages before profiles, or Qt WebEngine crashes on exit
    log.end_session()
    return exit_code
# --- END [2.1] ---

# ==============================================================================
# [3.1] run_as_module
# ==============================================================================
if __name__ == "__main__":
    sys.exit(main())
# --- END [3.1] ---
