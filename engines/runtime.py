# ==============================================================================
# FILE: engines/runtime.py
# OVERVIEW: The running DevOS's engines: packages found in engines/ and in the user's
#           <data>/engine-packages/, attached or detached (remembered across restarts), one bus they all sit
#           on, engine logs forwarded to the System Log, an orderly shutdown at exit.
# PURPOSE:
#   1. get_bus(): the shared Bus with every attached engine registered (in dependency order); engines start
#      lazily on their first request.
#   2. attach(id) / detach(id): checks "requires" (attach fails without them; detaching an engine another
#      attached engine requires is refused), remembers the choice (preference ENGINES_DETACHED), announces
#      "runtime.attached" / "runtime.detached" so views and menus follow. Detaching never deletes data.
#   3. Capability queries: producers(shape), answering(request), sources() (engines following the view
#      source contract); engine_state(id) for Settings → Engines.
#   4. add_package(folder): copy an engine package into the user's engine-packages folder.
# CONTRACT:
#   - Requires: engines.kernel (Bus, packages), app.data (activity_log, store.database; no Qt).
#   - Exposes: get_bus(), shutdown_engines(), packages(), package(id), rescan(), attach(id), detach(id),
#              engine_state(id), producers(shape), answering(request), sources(), add_package(folder),
#              user_packages_folder(), ENGINES_DETACHED, SOURCE_REQUESTS
# NOTES:
#   - [2026-11-17] Phase 4: core engines (Knowledge) can't be detached.
#   - [2026-11-17] Phase 3: engines come from packages (engine.json) instead of a fixed list; attach / detach,
#                  requires, capability queries, user engine packages.
#   - [2026-11-17] Created in Phase 1 of the engine plan, with the Knowledge stub.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ENGINES_DETACHED (str): User preference listing the engine ids the user detached.
# - SOURCE_REQUESTS (tuple): Requests an engine must answer to be a source views can show (collections,
#   schema, query - docs/views-api.md, "source contract").
# - user packages folder: <data>/engine-packages/<package>/ (engines you add; built-ins win on an id clash).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. RUNTIME
#    1.1 packages
#    1.2 bus
#    1.3 attach_detach
#    1.4 capabilities
# ==============================================================================

# ==============================================================================
# [1.1] packages
# ==============================================================================
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from app.data.activity_log import DEBUG, ERROR, INFO, SUCCESS, WARNING, get_activity_log
from app.data.store.database import data_directory, get_database
from engines.kernel import Bus, EngineError, Message
from engines.kernel.conformance import SOURCE_REQUESTS
from engines.kernel.packages import REPO_ENGINES, EnginePackage, PackageError, discover, load_engine_class, read_package

ENGINES_DETACHED = "engines.detached"
_LEVELS = {"debug": DEBUG, "info": INFO, "success": SUCCESS, "warning": WARNING, "error": ERROR}
_bus: Bus | None = None
_bus_lock = threading.RLock()
_packages: dict[str, EnginePackage] = {}
_failures: dict[str, str] = {}


def user_packages_folder() -> Path:
    return data_directory() / "engine-packages"


def rescan() -> list[str]:
    """Look for packages again (after adding one); returns the problems found."""
    found, problems = discover([REPO_ENGINES, user_packages_folder()])
    _packages.clear()
    _packages.update({package.engine_id: package for package in found})
    for problem in problems:
        get_activity_log().warning("engines", problem)
    return problems


def packages() -> list[EnginePackage]:
    if not _packages:
        rescan()
    return sorted(_packages.values(), key=lambda package: (not package.is_built_in, package.title.lower()))


def package(engine_id: str) -> EnginePackage | None:
    packages()
    return _packages.get(engine_id)


def add_package(folder: Path) -> EnginePackage:
    """Copy an engine package (a folder with engine.json) into the user's engine-packages folder."""
    source = read_package(Path(folder))
    if package(source.engine_id) is not None:
        raise PackageError(f"An engine called “{source.engine_id}” is already installed")
    target = user_packages_folder() / Path(folder).name
    if target.exists():
        raise PackageError(f"{target} already exists")
    shutil.copytree(folder, target, ignore=shutil.ignore_patterns("__pycache__"))
    rescan()
    get_activity_log().info("engines", f"Added engine package “{source.title}” ({source.version})")
    return package(source.engine_id)
# --- END [1.1] ---


# ==============================================================================
# [1.2] bus
# ==============================================================================
def _forward_log(message: Message) -> None:
    get_activity_log().log(_LEVELS.get(message.payload.get("level"), INFO), f"engine:{message.engine_id}",
                           str(message.payload.get("message", "")))


def _detached_ids() -> set[str]:
    return set(get_database().get_preference(ENGINES_DETACHED) or [])


def get_bus() -> Bus:
    """The shared bus, created on first use with every attached engine registered."""
    global _bus
    with _bus_lock:
        if _bus is None:
            bus = Bus()
            bus.subscribe("*.log", _forward_log)
            _bus = bus
            detached = {engine_id for engine_id in _detached_ids() if not (package(engine_id) and package(engine_id).is_core)}
            waiting = [item for item in packages() if item.engine_id not in detached]
            for item in packages():
                if item.engine_id in detached:
                    bus.detach(item.engine_id, item.title)
            while waiting:                            # dependency order: attach what has its requirements
                ready = [item for item in waiting if all(bus.is_attached(need) for need in item.requires)]
                if not ready:
                    break
                for item in ready:
                    waiting.remove(item)
                    _register(bus, item)
            for item in waiting:
                missing = [need for need in item.requires if not bus.is_attached(need)]
                _failures[item.engine_id] = f"needs {', '.join(missing)}"
                bus.detach(item.engine_id, item.title)
        return _bus


def _register(bus: Bus, item: EnginePackage) -> bool:
    try:
        bus.register(load_engine_class(item))
        _failures.pop(item.engine_id, None)
        return True
    except (PackageError, EngineError) as error:
        _failures[item.engine_id] = str(error)
        bus.detach(item.engine_id, item.title)
        get_activity_log().error("engines", f"{item.title} couldn't be attached", error)
        return False


def shutdown_engines() -> None:
    global _bus
    with _bus_lock:
        bus, _bus = _bus, None
    if bus is not None:
        bus.shutdown()
# --- END [1.2] ---


# ==============================================================================
# [1.3] attach_detach
# ==============================================================================
def attach(engine_id: str) -> None:
    """Attach an engine (and remember it). EngineError when its package or a requirement is missing."""
    bus, item = get_bus(), package(engine_id)
    if item is None:
        raise EngineError(f"No engine package called “{engine_id}”", code="unknown_engine")
    missing = [need for need in item.requires if not bus.is_attached(need)]
    if missing:
        raise EngineError(f"{item.title} needs {', '.join(missing)} – attach {'it' if len(missing) == 1 else 'them'} first",
                          code="requires")
    if not _register(bus, item):
        raise EngineError(f"{item.title} couldn't be attached: {_failures[engine_id]}", code="attach_failed")
    database = get_database()
    database.set_preference(ENGINES_DETACHED, sorted(_detached_ids() - {engine_id}))
    get_activity_log().success("engines", f"Attached {item.title}")
    bus.publish("runtime.attached", {"engine_id": engine_id})


def detach(engine_id: str) -> None:
    """Detach an engine (and remember it); its data stays where it is. Refused for core engines and while an
    attached engine requires it."""
    bus, item = get_bus(), package(engine_id)
    title = item.title if item else engine_id
    if item is not None and item.is_core:
        raise EngineError(f"{title} is a core engine: DevOS keeps it attached", code="core")
    dependents = [other.title for other in packages() if engine_id in other.requires and bus.is_attached(other.engine_id)]
    if dependents:
        raise EngineError(f"{', '.join(dependents)} {'needs' if len(dependents) == 1 else 'need'} {title} – "
                          f"detach {'it' if len(dependents) == 1 else 'them'} first", code="required")
    bus.detach(engine_id, title)
    database = get_database()
    database.set_preference(ENGINES_DETACHED, sorted(_detached_ids() | {engine_id}))
    get_activity_log().info("engines", f"Detached {title} (its data is kept)")
    bus.publish("runtime.detached", {"engine_id": engine_id})


def engine_state(engine_id: str) -> dict:
    """For Settings → Engines: attached, running, failure (why it isn't attached), missing requirements."""
    bus, item = get_bus(), package(engine_id)
    missing = [need for need in (item.requires if item else ()) if not bus.is_attached(need)]
    return {"attached": bus.is_attached(engine_id), "running": bus.is_running(engine_id), "core": bool(item and item.is_core),
            "failure": _failures.get(engine_id, ""), "missing": missing,
            "user_detached": engine_id in _detached_ids()}
# --- END [1.3] ---


# ==============================================================================
# [1.4] capabilities
# ==============================================================================
def producers(shape: str) -> list[str]:
    """Attached engines that produce a shape ("geo", "table" ...)."""
    return sorted(manifest.engine_id for manifest in get_bus().manifests() if shape in manifest.shapes)


def answering(request: str) -> list[str]:
    """Attached engines answering a request: a name ("style") or a full topic ("spatial.style")."""
    if "." in request:
        engine_id, name = request.split(".", 1)
        return [engine_id] if get_bus().is_attached(engine_id) and get_bus().manifest(engine_id).request(name) else []
    return sorted(manifest.engine_id for manifest in get_bus().manifests() if manifest.request(request))


def sources() -> list[str]:
    """Attached engines that follow the view source contract (views can show their data)."""
    return sorted(manifest.engine_id for manifest in get_bus().manifests()
                  if all(manifest.request(name) for name in SOURCE_REQUESTS))
# --- END [1.4] ---
