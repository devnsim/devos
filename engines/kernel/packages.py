# ==============================================================================
# FILE: engines/kernel/packages.py
# OVERVIEW: Engine packages - how an engine is found and loaded: a folder with an engine.json (what it is,
#           what it needs, what views it brings) and its Python code. DevOS lists packages without running
#           them; code is imported only when the engine is attached.
# PURPOSE:
#   1. read_package(folder) -> EnginePackage: engine.json checked (id, version, entry, kernel API, requires /
#      uses, bundled views).
#   2. discover(folders) -> packages found in each folder's subfolders (built-in engines/, the user's
#      <data>/engine-packages/); a package id appearing twice keeps the first (built-ins win).
#   3. load_engine_class(package): imports the entry ("module:Class") - a package outside the repo is
#      imported under its own private name, so its relative imports work and it can't shadow DevOS modules.
# CONTRACT:
#   - Requires: standard library only (engine code is imported on demand).
#   - Exposes: EnginePackage, PackageError, read_package(), discover(), load_engine_class(), load_package_module(),
#              KERNEL_API, PACKAGE_FILE
# NOTES:
#   - [2026-11-17] Phase 4: "core" packages (built-in only) can't be detached.
#   - [2026-11-17] Created in Phase 3 of the engine plan (docking).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - engine package: A folder holding PACKAGE_FILE (engine.json) and the engine's code.
# - KERNEL_API (int): Version of the kernel contract (messages, manifests, shapes) this DevOS speaks; a
#   package needing a newer one is refused.
# - entry (str): "module:Class" of the engine, relative to the package ("engine:KnowledgeEngine").
# - views (str): Optional module of the package that registers its view types (imported by the front end).
# - core (bool): A built-in engine DevOS relies on (Knowledge); it can't be detached. Only built-ins can be core.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PACKAGES
#    1.1 package
#    1.2 discover_and_load
# ==============================================================================

# ==============================================================================
# [1.1] package
# ==============================================================================
from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

KERNEL_API = 1
PACKAGE_FILE = "engine.json"
ENGINE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
REPO_ENGINES = Path(__file__).resolve().parents[1]               # engines/


class PackageError(Exception):
    """An engine package that can't be used; the message says why."""


@dataclass(frozen=True)
class EnginePackage:
    engine_id: str
    title: str
    version: str
    summary: str
    folder: Path
    entry: str
    kernel_api: int = KERNEL_API
    requires: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    views: str = ""
    is_built_in: bool = False
    is_core: bool = False                            # stays attached (other engines and DevOS itself rely on it)
    extra: dict = field(default_factory=dict)

    @property
    def module_prefix(self) -> str:
        """Import name of the package: engines.<id> for built-ins, a private name otherwise."""
        return f"engines.{self.folder.name}" if self.is_built_in else f"devos_engine_package_{self.engine_id}"


def read_package(folder: Path) -> EnginePackage:
    folder = Path(folder).resolve()
    try:
        data = json.loads((folder / PACKAGE_FILE).read_text())
    except (OSError, ValueError) as error:
        raise PackageError(f"{folder.name}: can't read {PACKAGE_FILE} ({error})") from None
    missing = [key for key in ("id", "title", "version", "entry") if not data.get(key)]
    if missing:
        raise PackageError(f"{folder.name}: {PACKAGE_FILE} lacks {', '.join(missing)}")
    if not ENGINE_ID.match(str(data["id"])):
        raise PackageError(f"{folder.name}: engine id “{data['id']}” must be lowercase letters, digits and _")
    if ":" not in str(data["entry"]):
        raise PackageError(f"{folder.name}: entry must be “module:Class”")
    if int(data.get("kernel_api", KERNEL_API)) > KERNEL_API:
        raise PackageError(f"{data['title']} needs kernel API {data['kernel_api']}; this DevOS has {KERNEL_API}")
    known = {"id", "title", "version", "summary", "entry", "kernel_api", "requires", "uses", "views", "core"}
    return EnginePackage(str(data["id"]), str(data["title"]), str(data["version"]), str(data.get("summary", "")), folder,
                         str(data["entry"]), int(data.get("kernel_api", KERNEL_API)), tuple(data.get("requires") or ()),
                         tuple(data.get("uses") or ()), str(data.get("views") or ""), folder.parent == REPO_ENGINES,
                         bool(data.get("core")) and folder.parent == REPO_ENGINES,
                         {key: value for key, value in data.items() if key not in known})
# --- END [1.1] ---


# ==============================================================================
# [1.2] discover_and_load
# ==============================================================================
def discover(folders: list[Path]) -> tuple[list[EnginePackage], list[str]]:
    """(packages, problems): every readable package in the folders' subfolders, first id wins."""
    packages: dict[str, EnginePackage] = {}
    problems = []
    for root in folders:
        if not Path(root).is_dir():
            continue
        for folder in sorted(Path(root).iterdir()):
            if not (folder / PACKAGE_FILE).is_file():
                continue
            try:
                package = read_package(folder)
            except PackageError as error:
                problems.append(str(error))
                continue
            if package.engine_id in packages:
                problems.append(f"{folder}: engine id “{package.engine_id}” is already provided by "
                                f"{packages[package.engine_id].folder}")
                continue
            packages[package.engine_id] = package
    return list(packages.values()), problems


def _import_package(package: EnginePackage) -> None:
    """Make the package importable under package.module_prefix (built-ins already are)."""
    if package.is_built_in or package.module_prefix in sys.modules:
        return
    init = package.folder / "__init__.py"
    if not init.exists():                              # a plain folder: a namespace for its modules
        module = types.ModuleType(package.module_prefix)
        module.__path__ = [str(package.folder)]
        sys.modules[package.module_prefix] = module
        return
    spec = importlib.util.spec_from_file_location(package.module_prefix, init,
                                                  submodule_search_locations=[str(package.folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[package.module_prefix] = module
    spec.loader.exec_module(module)


def load_package_module(package: EnginePackage, module_name: str):
    """Import one module of a package ("engine", "views" ...)."""
    _import_package(package)
    try:
        return importlib.import_module(f"{package.module_prefix}.{module_name}")
    except Exception as error:
        raise PackageError(f"{package.title}: importing {module_name} failed: {error}") from error


def load_engine_class(package: EnginePackage):
    module_name, class_name = package.entry.split(":", 1)
    engine_class = getattr(load_package_module(package, module_name), class_name, None)
    if engine_class is None:
        raise PackageError(f"{package.title}: {package.entry} not found")
    manifest = getattr(engine_class, "MANIFEST", None)
    if manifest is None or manifest.engine_id != package.engine_id:
        raise PackageError(f"{package.title}: {PACKAGE_FILE} says “{package.engine_id}” but the engine's manifest says "
                           f"“{getattr(manifest, 'engine_id', None)}”")
    return engine_class
# --- END [1.2] ---
