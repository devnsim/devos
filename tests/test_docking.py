# ==============================================================================
# FILE: tests/test_docking.py
# OVERVIEW: Phase 3 of the engine plan - docking and standards: engine packages, the conformance kit, request
#           timeouts, busy engines, stream back-pressure, attach / detach with placeholders that come back, engine-
#           bundled views, capability queries, Settings → Engines and the bus inspector.
# PURPOSE:
#   1. The done-when: an engine built in its own folder (examples/engines/sample_data) passes the kit and docks
#      from the user's engine-packages folder without changes to DevOS; detaching it leaves its views showing
#      placeholders that come back to life when it is re-attached.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures, engines (kernel, runtime), app.views, app.ui (settings page, bus
#               inspector, engine bridge), examples/engines/sample_data, tools/check_standard.py
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-17] The Settings test detaches Records: Knowledge is a core engine now (Detach is off for it).
#   - [2026-11-17] Created in Phase 3 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SAMPLE (Path): The example engine package (examples/engines/sample_data).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. HELPERS
#    1.1 helpers
# 2. TESTS
#    2.1 packages_and_kit
#    2.2 robustness
#    2.3 docking
#    2.4 settings_and_inspector
# ==============================================================================

# ==============================================================================
# [1.1] helpers
# ==============================================================================
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from engines.kernel import Bus, Engine, EngineError, EngineManifest, RequestSpec

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "engines" / "sample_data"


class SlowEngine(Engine):
    MANIFEST = EngineManifest("slow", "Slow", "1", "Waits on purpose", requests=(
        RequestSpec("wait", "Wait until released", timeout_s=0.3),
        RequestSpec("hold", "Block until the gate opens"),
        RequestSpec("many", "Many items, fast", params=("n",), streams=True)))

    gate = threading.Event()

    def on_wait(self, _payload, _job):
        time.sleep(1.0)
        return {}

    def on_hold(self, _payload, _job):
        SlowEngine.gate.wait(5)
        return {}

    def on_many(self, payload, _job):
        for number in range(payload["n"]):
            yield {"n": number}


def row_of(page, title: str):
    tree = page.engines_tree
    return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == title)


def write_package(folder: Path, data: dict, code: str = "") -> Path:
    folder.mkdir(parents=True)
    (folder / "engine.json").write_text(json.dumps(data))
    if code:
        (folder / "engine.py").write_text(code)
    return folder
# --- END [1.1] ---


# ==============================================================================
# [2.1] packages_and_kit
# ==============================================================================
def test_packages_are_read_without_running_them_and_checked(tmp_path):
    from engines.kernel.packages import PackageError, discover, read_package
    good = write_package(tmp_path / "one", {"id": "one", "title": "One", "version": "1", "entry": "engine:One"})
    assert read_package(good).engine_id == "one" and not read_package(good).is_built_in
    for name, data, problem in (("bad_id", {"id": "Bad-Id", "title": "x", "version": "1", "entry": "e:E"}, "lowercase"),
                                ("no_entry", {"id": "x", "title": "x", "version": "1"}, "lacks entry"),
                                ("future", {"id": "y", "title": "Y", "version": "1", "entry": "e:E", "kernel_api": 99},
                                 "kernel API 99")):
        with pytest.raises(PackageError, match=problem):
            read_package(write_package(tmp_path / name, data))
    write_package(tmp_path / "zz_clash", {"id": "one", "title": "Clash", "version": "2", "entry": "engine:One"})
    found, problems = discover([tmp_path])
    assert [package.engine_id for package in found] == ["one"] and any("already provided" in item for item in problems)


def test_conformance_kit_passes_good_engines_and_names_what_is_wrong(tmp_path):
    from engines.kernel.conformance import check_engine, check_package
    for folder in (ROOT / "engines" / "knowledge", ROOT / "engines" / "records", SAMPLE):
        checks = check_package(folder)
        assert all(check.ok for check in checks), [check for check in checks if not check.ok]

    class Sloppy(Engine):
        MANIFEST = EngineManifest("sloppy", "Sloppy", "1", "", shapes=("Table",), requests=(
            RequestSpec("list", "Lists", example={}, shape="table"), RequestSpec("forgotten", "No handler")))

        def on_list(self, _payload, _job):
            return {"not": "a table"}
    failed = {check.name: check.detail for check in check_engine(Sloppy) if not check.ok}
    assert set(failed) == {"manifest is valid", "every request has a handler", "list answers its example"}
    assert "unknown shape “Table”" in failed["manifest is valid"] and "on_forgotten" in failed["every request has a handler"]
    run = subprocess.run([sys.executable, "-m", "engines.kernel.conformance", str(SAMPLE)], capture_output=True,
                         text=True, timeout=120, cwd=ROOT)              # the command line (its own temporary data)
    assert run.returncode == 0 and "13 of 13 checks passed" in run.stdout
# --- END [2.1] ---


# ==============================================================================
# [2.2] robustness
# ==============================================================================
def test_timeouts_busy_engines_and_stream_back_pressure(monkeypatch):
    from engines.kernel import hosts
    bus = Bus()
    bus.register(SlowEngine)
    with pytest.raises(EngineError) as late:
        bus.call("slow.wait")                                          # its own timeout_s (0.3 s) applies
    assert late.value.code == "timeout" and bus.stats()["slow"]["timeouts"] == 1
    time.sleep(0.9)                                                    # the late handler still runs to its end (actor)

    received, ended = [], threading.Event()
    handle = bus.stream("slow.many", {"n": 50}, lambda message: received.append(message.payload["n"]),
                        lambda cancelled, error: ended.set(), window=5)
    time.sleep(0.3)
    assert received == [0, 1, 2, 3, 4]                                 # the engine waits for the receiver
    handle.ack(5)
    time.sleep(0.3)
    assert len(received) == 10 and not ended.is_set()
    handle.ack(1000)
    assert ended.wait(5) and received == list(range(50))
    bus.shutdown()

    monkeypatch.setattr(hosts, "INBOX_LIMIT", 2)
    busy_bus = Bus()
    busy_bus.register(SlowEngine)
    SlowEngine.gate.clear()
    first = busy_bus.request("slow.hold")                              # handled: blocks the actor
    time.sleep(0.1)
    queued = [busy_bus.request("slow.hold") for _ in range(2)]         # fills the inbox
    with pytest.raises(EngineError) as busy:
        busy_bus.request("slow.hold").result(2)
    assert busy.value.code == "busy"
    SlowEngine.gate.set()
    for future in [first] + queued:
        future.result(5)
    stats = busy_bus.stats()["slow"]
    assert stats["requests"] == 4 and stats["errors"] == 1 and stats["max_ms"] > 0
    busy_bus.shutdown()


def test_latest_only_delivery_keeps_a_slow_screen_current(qapp, wait_for):
    from PySide6.QtWidgets import QWidget

    from app.ui.engine_bridge import stream_engine
    from engines.runtime import get_bus
    get_bus().register(SlowEngine)
    owner, shown, ended = QWidget(), [], []
    stream_engine(owner, "slow.many", {"n": 2000}, lambda message: (shown.append(message.payload["n"]), time.sleep(0.002)),
                  lambda cancelled, error: ended.append(error), window=50, latest=True)
    assert wait_for(lambda: ended, 10) and ended == [None]
    assert shown[-1] == 1999 and len(shown) < 2000                     # skipped the backlog, never lost the newest
    owner.deleteLater()
# --- END [2.2] ---


# ==============================================================================
# [2.3] docking
# ==============================================================================
def test_an_engine_built_elsewhere_docks_and_its_views_survive_detaching(qapp, database, pump, wait_for, tmp_path):
    from app.views.api import make_definition, view_type
    from app.views.host import ViewHost
    from engines import runtime
    bus = runtime.get_bus()
    target = runtime.user_packages_folder() / "sample_data"
    shutil.rmtree(target, ignore_errors=True)
    added = runtime.add_package(SAMPLE)                                # copied into <data>/engine-packages
    assert added.engine_id == "sample_data" and not added.is_built_in and target.is_dir()
    assert runtime.engine_state("sample_data")["attached"] is False    # found, not attached yet
    runtime.attach("sample_data")
    assert "sample_data" in runtime.sources() and "sample_data" in runtime.producers("table")
    assert runtime.answering("ticker") == ["sample_data"] and runtime.answering("sample_data.query") == ["sample_data"]

    source = {"engine": "sample_data", "collection": "orders"}
    table = ViewHost(make_definition("table", source, {"filter": {"field": "region", "op": "eq", "value": "North"}}))
    totals = ViewHost(make_definition("totals", source, mapping={"group": "region", "value": "amount"}))
    table.show(), totals.show()
    assert view_type("totals") is not None and view_type("totals").engine == "sample_data"     # an engine's own view
    assert wait_for(lambda: table.data is not None and totals.data is not None and totals.view.totals, 5)
    assert table.data.total > 0 and {row["region"] for row in table.data.rows} == {"North"}
    assert {key for key, _total in totals.view.totals} == {"North", "South", "East", "West"}

    runtime.detach("sample_data")                                      # placeholders, definitions kept
    assert wait_for(lambda: table.view_message_label.isVisible() and "isn't attached" in table.view_message_label.text(), 5)
    assert wait_for(lambda: totals.view is None and "Settings → Engines" in totals.view_message_label.text(), 5)
    assert totals.block_state()["view_type"] == "totals" and table.block_state()["query"]["filter"]["value"] == "North"
    with pytest.raises(EngineError) as gone:
        bus.call("sample_data.collections")
    assert gone.value.code == "not_attached" and "Settings → Engines" in str(gone.value)
    assert "sample_data" in (database.get_preference(runtime.ENGINES_DETACHED) or [])

    runtime.attach("sample_data")                                      # ... and they come back
    assert wait_for(lambda: totals.view is not None and totals.view.totals and not table.view_message_label.isVisible(), 5)

    needs = write_package(runtime.user_packages_folder() / "needs_sample", {
        "id": "needs_sample", "title": "Needs sample", "version": "1", "entry": "engine:NeedsSample",
        "requires": ["sample_data"]}, "from engines.kernel import Engine, EngineManifest\n\n\nclass NeedsSample(Engine):\n"
        "    MANIFEST = EngineManifest('needs_sample', 'Needs sample', '1', 'Requires sample data')\n")
    runtime.rescan()
    runtime.attach("needs_sample")
    with pytest.raises(EngineError, match="Needs sample needs Sample data"):
        runtime.detach("sample_data")                                  # refused while something requires it
    runtime.detach("needs_sample")
    runtime.detach("sample_data")
    with pytest.raises(EngineError, match="needs sample_data"):
        runtime.attach("needs_sample")
    for host in (table, totals):
        host.deleteLater()
    shutil.rmtree(needs)
    shutil.rmtree(target)
    runtime.rescan()


def test_engines_never_import_each_other():
    sys.path.insert(0, str(ROOT / "tools"))
    from check_standard import boundary_errors
    assert boundary_errors("engines/spatial/engine.py", ["from engines.records.engine import RecordsEngine"])
    assert not boundary_errors("engines/spatial/engine.py", ["from engines.kernel import Bus", "from engines.toolkit import table"])
    assert not boundary_errors("examples/engines/sample_data/views.py", ["from PySide6.QtWidgets import QWidget"])
    assert boundary_errors("examples/engines/sample_data/engine.py", ["from PySide6.QtWidgets import QWidget"])
    assert boundary_errors("engines/spatial/views.py", ["from app.modules.map.view import MapModule"])
# --- END [2.3] ---


# ==============================================================================
# [2.4] settings_and_inspector
# ==============================================================================
def test_settings_engines_section_and_bus_inspector(qapp, database, wait_for, monkeypatch):
    from app.ui.bus_inspector import open_bus_inspector
    from app.ui.pages.settings_page import SettingsPage
    from engines.runtime import get_bus
    page = SettingsPage()
    page.show()
    rows = {page.engines_tree.topLevelItem(i).data(0, 0x0100): page.engines_tree.topLevelItem(i)
            for i in range(page.engines_tree.topLevelItemCount())}
    assert {"knowledge", "records"} <= set(rows) and rows["records"].text(4) == "built in"
    assert rows["knowledge"].text(4) == "built in · core"
    rows["knowledge"].setSelected(True)                       # a core engine: Detach is off, and refused anyway
    assert not page.detach_btn.isEnabled() and not page.attach_btn.isEnabled()
    page._attach_or_detach(False)
    assert "core engine" in page.engine_status_label.text() and get_bus().is_attached("knowledge")
    page.engines_tree.clearSelection()
    row_of(page, "Records").setSelected(True)
    assert page.detach_btn.isEnabled() and not page.attach_btn.isEnabled()
    page._attach_or_detach(False)
    assert wait_for(lambda: row_of(page, "Records").text(2) == "Detached", 5)
    row_of(page, "Records").setSelected(True)
    page._attach_or_detach(True)
    assert wait_for(lambda: row_of(page, "Records").text(2).startswith("Attached"), 5)
    row_of(page, "Records").setSelected(True)
    page.check_selected_engine()
    assert wait_for(lambda: "checks passed" in page.engine_status_label.text(), 20)
    assert "✘" not in page.engine_status_label.toolTip()

    inspector = open_bus_inspector()
    get_bus().call("knowledge.list", {})
    assert wait_for(lambda: inspector.messages_tree.topLevelItemCount() >= 2, 5)
    topics = {inspector.messages_tree.topLevelItem(i).text(2) for i in range(inspector.messages_tree.topLevelItemCount())}
    assert "knowledge.list" in topics
    inspector.refresh_engines()
    titles = [inspector.engines_tree.topLevelItem(i).text(0) for i in range(inspector.engines_tree.topLevelItemCount())]
    assert "Knowledge" in titles
    inspector.close()
    page.close()
# --- END [2.4] ---
