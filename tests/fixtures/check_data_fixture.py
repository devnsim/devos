# ==============================================================================
# FILE: tests/fixtures/check_data_fixture.py
# OVERVIEW: Opens a (copied) fixture data folder with the CURRENT app and reports what it finds, as JSON on
#           the last line of stdout. tests/test_data_safety.py compares the report with <fixture>.expected.json.
# PURPOSE:
#   1. Run in its own process so the fixture gets fresh singletons (database, stores, log) and the test
#      run's own data folder is never touched.
#   2. Opens every page the way a user would (page widgets, module tiles, Canvas cards), then reads the
#      user data back through today's APIs. When the engine plan moves data, THIS file changes to read it
#      from its new home; the expectations never change.
# USAGE:
#   python tests/fixtures/check_data_fixture.py COPY_OF_FIXTURE_FOLDER
# CONTRACT:
#   - Requires: the DevOS app, offscreen platform. Writes only inside the given folder.
#   - Exposes: a JSON report (same keys as the expectations, plus "errors": error-level log messages)
# NOTES:
#   - [2026-11-17] Phase 5c: the user database is found through library() (the Database Editor is now a template).
#   - [2026-11-17] Reads the Library through library() (the Knowledge engine owns it since Phase 4).
#   - [2026-11-17] Waits for page titles to settle (a browser page renames itself while its site loads).
#   - [2026-11-17] Created in Phase 0 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - report (dict): What the current app finds in the fixture.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHECK
#    1.1 environment
#    1.2 report
# ==============================================================================

# ==============================================================================
# [1.1] environment
# ==============================================================================
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

FOLDER = Path(sys.argv[1]).resolve()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["DEVOS_DATA_DIR"] = str(FOLDER)
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# --- END [1.1] ---


# ==============================================================================
# [1.2] report
# ==============================================================================
def pump(app, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def report() -> dict:
    from PySide6.QtWidgets import QApplication

    from app.ui.web_support import prepare_web_engine
    prepare_web_engine()
    app = QApplication(sys.argv)
    from app.data.activity_log import ERROR, get_activity_log
    from app.clients.library import library
    from app.data.store.database import get_database
    from app.data.store.pages import PAGE_KIND_CANVAS, PAGE_KIND_INFINITE_CANVAS
    from app.data.store.records import get_collection
    from app.modules.agents.store import get_chat_store
    from app.modules.comms.engine import CommsStore
    from app.modules.map.store import MapStore
    from app.ui.main_window import MainWindow
    database = get_database()
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    pump(app)

    page_modules, canvas_cards = {}, {}
    for page in window.page_store.pages():
        widget = window.page_widget(page.page_id)          # opened the way the sidebar opens it
        pump(app, 0.1)
        if page.kind == PAGE_KIND_CANVAS and widget.module_canvas.tiles:
            page_modules[page.title] = sorted(tile.kind for tile in widget.module_canvas.tiles.values())
        elif page.kind == PAGE_KIND_INFINITE_CANVAS:
            canvas_cards[page.title] = sorted(card.kind for card in widget.infinite_canvas.cards)
    titles, deadline = None, time.time() + 15          # browser pages rename themselves while their sites load
    while time.time() < deadline:
        current = [page.title for page in window.page_store.pages()]
        if current == titles:
            break
        titles = current
        pump(app, 1.0)
    store = library()
    library = [{"kind": item.kind, "name": item.name, "sha256": hashlib.sha256(store.path(item).read_bytes()).hexdigest()}
               for kind in ("note", "dataset") for item in store.items(kind) if item.name.startswith("Fixture")]
    user_db = next(item for item in store.items("database") if item.name == "Fixture inventory")
    connection = sqlite3.connect(store.path(user_db))
    rows = [list(row) for row in connection.execute("SELECT name, qty FROM parts ORDER BY part_id")]
    connection.close()
    agents = get_collection("agents", [], database)
    helper = next(record for record in agents.records() if record.title == "Fixture helper")
    chats = get_chat_store(database)
    conversation = chats.conversations(helper.record_id)[0]
    result = {
        "pages": sorted([{"title": page.title, "kind": page.kind, "favorite": page.is_favorite}
                         for page in window.page_store.pages()], key=lambda item: item["title"]),
        "page_modules": page_modules,
        "canvas_cards": canvas_cards,
        "folders": [folder.name for folder in window.page_store.folders()],
        "records": {"projects": sorted(record.title for record in get_collection("projects", [], database).records()),
                    "agents": sorted(record.title for record in agents.records())},
        "chat": [[message.role, message.content] for message in chats.messages(conversation.conversation_id)],
        "library": sorted(library, key=lambda item: item["name"]),
        "user_database": {"name": user_db.name, "rows": rows},
        "places": [place.name for place in MapStore(database).places()],
        "mail_accounts": [account.name for account in CommsStore(database).accounts()],
        "preference": ["fixture.marker", database.get_preference("fixture.marker")],
        "errors": [f"{entry.source}: {entry.message}" for entry in get_activity_log().entries if entry.level >= ERROR],
    }
    for each in list(window.workspace.windows):
        each.close()
    pump(app, 0.3)
    return result


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False), flush=True)
    os._exit(0)                                       # skip Qt WebEngine's teardown at interpreter exit
# --- END [1.2] ---
