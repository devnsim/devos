# ==============================================================================
# FILE: tests/conftest.py
# OVERVIEW: Shared pytest setup: headless Qt, a throw-away database, and a QApplication fixture.
# PURPOSE:
#   1. Forces QT_QPA_PLATFORM=offscreen and a temporary DEVOS_DATA_DIR before anything imports Qt or
#      opens the database, so tests never touch the user's data or screen.
#   2. qapp: one QApplication (with the DevOS stylesheet) for the whole run; pump(): let Qt process events.
# CONTRACT:
#   - Requires: pytest, PySide6
#   - Exposes: fixtures qapp, database, pump, wait_for
# NOTES:
#   - [2026-11-17] The engines' bus is shut down at the end of the run.
#   - [2026-11-17] Tests use keyring's null backend and no provider API keys (Agents chat, Comms passwords).
#   - [2026-11-16] qapp tears Qt WebEngine down in order at the end (the run exited with a segfault).
#   - [2026-11-16] qapp prepares Qt WebEngine before creating the QApplication (browser page tests).
#   - [2026-11-15] Created in the health pass (tests used to live in throw-away scripts).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - pump(seconds): run the Qt event loop for a while (timers, deferred moves).
# - wait_for(condition): run the event loop until condition() holds - prefer it over fixed sleeps.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SETUP
#    1.1 environment
#    1.2 fixtures
# ==============================================================================

# ==============================================================================
# [1.1] environment
# ==============================================================================
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["DEVOS_DATA_DIR"] = tempfile.mkdtemp(prefix="devos_tests_")
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"   # never the user's real keyring
for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):        # never a real provider call
    os.environ.pop(name, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
# --- END [1.1] ---


# ==============================================================================
# [1.2] fixtures
# ==============================================================================
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from app.ui.theme import STYLESHEET
    from app.ui.web_support import prepare_web_engine
    prepare_web_engine()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    yield app
    from app.ui.web_support import shutdown_web_engine
    from engines.runtime import shutdown_engines
    for widget in QApplication.topLevelWidgets():
        widget.close()
    shutdown_engines()
    shutdown_web_engine()


@pytest.fixture(scope="session")
def database():
    from app.data.store.database import get_database
    return get_database()


@pytest.fixture
def wait_for(qapp):
    """wait_for(condition, timeout_s): pump Qt until condition() is true (debounced saves etc.)."""
    def run(condition, timeout_s: float = 3.0) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            qapp.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return condition()
    return run


@pytest.fixture
def pump(qapp):
    def run(seconds: float = 0.2) -> None:
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.003)
    return run
# --- END [1.2] ---
