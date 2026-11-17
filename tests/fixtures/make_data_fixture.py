# ==============================================================================
# FILE: tests/fixtures/make_data_fixture.py
# OVERVIEW: Builds a frozen DevOS data folder with the app as it is today, so later versions can prove they
#           still open old data (pages, layouts, records, chats, library files, user databases ...).
# PURPOSE:
#   1. Drive the real app offscreen: pages holding every module, a folder, a favourite, a Canvas page with
#      cards, a browser page; records (projects, agents), an agent chat, a note, a dataset, a user SQLite
#      database with rows, a map place, a mail account, a preference.
#   2. Write <name>.expected.json beside the folder: what a later version must find in it.
#   Run once per data-format generation (data_v0 = the app before the engine plan, 2026-11-17); never
#   regenerate an existing fixture - its whole point is that it stays old.
# USAGE:
#   nix-shell --run "python tests/fixtures/make_data_fixture.py tests/fixtures/data_v0"
# CONTRACT:
#   - Requires: the DevOS app (PySide6, Qt WebEngine), offscreen platform. Writes only the target folder.
#   - Exposes: the fixture folder and its .expected.json
# NOTES:
#   - [2026-11-17] Created in Phase 0 of the engine plan (docs/architecture/engine-plan.md).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - PAGES: The canvas pages the fixture holds and the module kinds on each.
# - expected (dict): The facts a later version must still find (written to <folder>.expected.json).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. FIXTURE
#    1.1 environment
#    1.2 build
#    1.3 freeze
# ==============================================================================

# ==============================================================================
# [1.1] environment
# ==============================================================================
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

TARGET = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if TARGET is None or TARGET.exists():
    sys.exit("usage: make_data_fixture.py NEW_FOLDER   (the folder must not exist yet)")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["DEVOS_DATA_DIR"] = str(TARGET)
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PAGES = {
    "Data": ["db_editor", "spreadsheet", "graph_db", "map", "graph"],
    "Planning": ["projects", "calendar", "agents"],
    "Engineering": ["cad", "wave_sim", "tensor_inspector"],
    "Tools": ["notes", "library", "trading_chart"],
    "Comms and system": ["comms", "monitor", "web_app"],
}
# --- END [1.1] ---


# ==============================================================================
# [1.2] build
# ==============================================================================
def pump(app, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def build() -> dict:
    from PySide6.QtWidgets import QApplication

    from app.ui.web_support import prepare_web_engine
    prepare_web_engine()
    app = QApplication(sys.argv)
    from app.data.activity_log import get_activity_log
    from app.data.store.content import get_content_store
    from app.data.store.database import get_database
    from app.data.store.pages import PAGE_KIND_INFINITE_CANVAS
    from app.data.store.records import get_collection
    from app.modules.agents.store import get_chat_store
    from app.modules.agents.view import AGENT_PROPERTIES
    from app.modules.comms.engine import CommsStore, MailAccount
    from app.modules.db_editor.engine import UserDatabases
    from app.modules.map.store import MapStore
    from app.modules.projects.view import PROJECT_PROPERTIES
    from app.ui.main_window import MainWindow
    from app.ui.theme import STYLESHEET
    app.setStyleSheet(STYLESHEET)
    database = get_database()
    log = get_activity_log()
    log.attach_database(database)
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    pump(app)

    for title, kinds in PAGES.items():
        page_id = window.create_page()
        window.rename_page(page_id, title)
        page = window.page_widget(page_id)
        for kind in kinds:
            page.module_canvas.add_library_module(kind)
        pump(app, 0.5)
        page.module_canvas.save_layout()
    window.page_store.set_favorite(next(p.page_id for p in window.page_store.pages() if p.title == "Planning"), True)
    folder_id = window.page_store.create_folder("Archive")
    window.page_store.create_page("Old notes", folder_id=folder_id)
    canvas_id = window.create_page()
    window.rename_page(canvas_id, "Board")
    window.convert_page(canvas_id, PAGE_KIND_INFINITE_CANVAS)
    canvas = window.page_widgets[canvas_id].infinite_canvas
    canvas.add_card("notes", cell=(2, 2), size=(20, 12))
    canvas.add_card("calendar", cell=(24, 2), size=(16, 12))
    canvas.save()
    browser = window.open_browser_tab("data:text/html,<title>Fixture site</title><p>fixture</p>")
    pump(app, 1.0)

    projects = get_collection("projects", PROJECT_PROPERTIES, database)
    for title, values in (("Ship v1", {"status": "Active", "priority": "High", "start": "2026-03-01", "due": "2026-03-20",
                                       "progress": 40, "tags": ["release"]}),
                          ("Write docs", {"status": "Planned", "owner": "me", "description": "User guide"}),
                          ("Old idea", {"status": "Idea"})):
        projects.create(title, values)
    agents = get_collection("agents", AGENT_PROPERTIES, database)
    helper = agents.create("Fixture helper", {"provider": "Ollama", "model": "llama3.2", "status": "Active",
                                              "instructions": "Answer briefly.", "permissions": ["Read files"]})
    agents.create("Reviewer", {"provider": "Claude", "model": "claude-sonnet-5-5", "role": "Reviews changes"})
    chats = get_chat_store(database)
    conversation = chats.new_conversation(helper.record_id, "First chat")
    chats.add_message(conversation.conversation_id, "user", "Hello?")
    chats.add_message(conversation.conversation_id, "assistant", "Hi! How can I help?", "llama3.2")

    store = get_content_store()
    note = store.create_text("note", "Fixture note", "# Fixture\n\nThis note must survive every migration.\n", ".md")
    dataset = store.create_text("dataset", "Fixture dataset", "x,y\n1,2\n2,4\n3,9\n", ".csv")
    user_db = UserDatabases().create("Fixture inventory")
    connection = sqlite3.connect(user_db.path)
    connection.executescript("CREATE TABLE parts (part_id INTEGER PRIMARY KEY, name TEXT NOT NULL, qty INTEGER);"
                             "INSERT INTO parts (name, qty) VALUES ('bolt', 40), ('nut', 55), ('washer', 12);")
    connection.commit()
    connection.close()
    MapStore(database).add("Workshop", 52.3702, 4.8952, "fixture place")
    CommsStore(database).save_account(MailAccount(0, "Fixture mail", "me@example.com", "me", "imap.example.com", 993,
                                                  "smtp.example.com", 465))
    database.set_preference("fixture.marker", "data_v0")
    pump(app, 0.6)

    expected = {
        "generation": TARGET.name,
        "made_at": time.strftime("%Y-%m-%d"),
        "pages": sorted([{"title": page.title, "kind": page.kind, "favorite": page.is_favorite}
                         for page in window.page_store.pages()], key=lambda item: item["title"]),
        "page_modules": {page.title: sorted(tile.kind for tile in window.page_widget(page.page_id).module_canvas.tiles.values())
                         for page in window.page_store.pages() if page.kind == "canvas"
                         and window.page_widget(page.page_id).module_canvas.tiles},
        "canvas_cards": {"Board": ["calendar", "notes"]},
        "folders": [folder.name for folder in window.page_store.folders()],
        "records": {"projects": sorted(record.title for record in projects.records()),
                    "agents": sorted(record.title for record in agents.records())},
        "chat": [["user", "Hello?"], ["assistant", "Hi! How can I help?"]],
        "library": sorted([{"kind": item.kind, "name": item.name,
                            "sha256": hashlib.sha256(store.path(item).read_bytes()).hexdigest()}
                           for item in (note, dataset)], key=lambda item: item["name"]),
        "user_database": {"name": "Fixture inventory", "rows": [["bolt", 40], ["nut", 55], ["washer", 12]]},
        "places": ["Workshop"],
        "mail_accounts": ["Fixture mail"],
        "preference": ["fixture.marker", "data_v0"],
    }
    browser.deleteLater()
    for each in list(window.workspace.windows):
        each.close()
    pump(app, 0.5)
    log.end_session()
    from app.ui.web_support import shutdown_web_engine
    shutdown_web_engine()
    database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database.close()
    return expected
# --- END [1.2] ---


# ==============================================================================
# [1.3] freeze
# ==============================================================================
def freeze(expected: dict) -> None:
    """Drop what isn't data (browser profiles, caches, side files, backups) and write the expectations."""
    for name in ("browser", "browser-containers", "backups", "devos.lock"):
        path = TARGET / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for path in TARGET.rglob("*"):
        if path.name.endswith(("-wal", "-shm", "-journal")):
            path.unlink()
    TARGET.with_name(TARGET.name + ".expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False) + "\n")
    size = sum(path.stat().st_size for path in TARGET.rglob("*") if path.is_file())
    print(f"Fixture {TARGET.name}: {sum(1 for p in TARGET.rglob('*') if p.is_file())} files, {size / 1024:.0f} KB", flush=True)


if __name__ == "__main__":
    freeze(build())
    os._exit(0)                                       # skip Qt WebEngine's teardown at interpreter exit
# --- END [1.3] ---
