# ==============================================================================
# FILE: engines/kernel/conformance.py
# OVERVIEW: The conformance kit - the standard checks every engine passes before it docks. Build an engine in
#           its own folder, run the kit, dock it. Used by the tests, Settings → Engines (Check) and the command line.
# PURPOSE:
#   1. check_package(folder) / check_engine(engine_class, package): manifest valid and matching engine.json,
#      every request has a handler, it starts and reports health, unknown requests are refused, example
#      requests answer (with valid shapes), streams can be cancelled, the source contract holds where claimed,
#      it stops cleanly.
#   2. Command line: python -m engines.kernel.conformance ENGINE_FOLDER [--data DIR] - runs against a temporary
#      data folder unless told otherwise, so checks never touch your data. Exit code 0 = every check passed.
# CONTRACT:
#   - Requires: engines.kernel (bus, messages, packages, shapes). Standard library + the engine's own needs.
#   - Exposes: Check, check_package(folder), check_engine(engine_class, package=None), report(checks) -> str,
#              SOURCE_REQUESTS, main(argv)
# NOTES:
#   - [2026-11-17] Created in Phase 3 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - check: One named conformance rule with its outcome and a detail line.
# - SOURCE_REQUESTS (tuple): Requests that make an engine a source views can show: collections, schema, query
#   (docs/views-api.md, "source contract").
# - CHECK_TIMEOUT_S (float): Longest any single check waits (default: 10).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CONFORMANCE
#    1.1 checks
#    1.2 command_line
# ==============================================================================

# ==============================================================================
# [1.1] checks
# ==============================================================================
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from engines.kernel.bus import Bus
from engines.kernel.messages import EngineError
from engines.kernel.packages import ENGINE_ID, EnginePackage, PackageError, load_engine_class, read_package
from engines.kernel.shapes import SHAPES, validate

SOURCE_REQUESTS = ("collections", "schema", "query")
CHECK_TIMEOUT_S = 10.0
NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def check_package(folder: Path) -> list[Check]:
    try:
        package = read_package(Path(folder))
        engine_class = load_engine_class(package)
    except PackageError as error:
        return [Check("package loads", False, str(error))]
    return [Check("package loads", True, f"{package.title} {package.version}")] + check_engine(engine_class, package)


def _call_stream(bus: Bus, topic: str, payload: dict, cancel_after: int | None) -> tuple[list, bool | None, EngineError | None]:
    items, ended = [], threading.Event()
    outcome: dict = {}
    handle = None

    def on_item(message) -> None:
        items.append(message)
        if cancel_after is not None and len(items) >= cancel_after and handle is not None:
            handle.cancel()

    def on_end(cancelled, error) -> None:
        outcome.update(cancelled=cancelled, error=error)
        ended.set()
    handle = bus.stream(topic, payload, on_item, on_end)
    if cancel_after == 0 and handle is not None:
        handle.cancel()
    if not ended.wait(CHECK_TIMEOUT_S):
        return items, None, EngineError("no end of stream", code="timeout")
    return items, outcome.get("cancelled"), outcome.get("error")


def check_engine(engine_class, package: EnginePackage | None = None) -> list[Check]:
    checks: list[Check] = []
    manifest = engine_class.MANIFEST
    engine_id = manifest.engine_id
    problems = []
    if not ENGINE_ID.match(engine_id):
        problems.append(f"id “{engine_id}”")
    if not manifest.title or not manifest.version:
        problems.append("title and version are required")
    problems += [f"request name “{spec.name}”" for spec in manifest.requests if not NAME.match(spec.name)]
    problems += [f"event name “{event}”" for event in manifest.events if not NAME.match(event)]
    problems += [f"unknown shape “{shape}”" for shape in manifest.shapes if shape not in SHAPES]
    problems += [f"{spec.name} answers unknown shape “{spec.shape}”" for spec in manifest.requests
                 if spec.shape and spec.shape not in SHAPES]
    checks.append(Check("manifest is valid", not problems, "; ".join(problems)))
    if package is not None:
        same = package.engine_id == engine_id and package.version == manifest.version
        checks.append(Check("engine.json matches the manifest", same,
                            "" if same else f"engine.json {package.engine_id} {package.version}, manifest {engine_id} "
                                            f"{manifest.version}"))
    engine = engine_class.__new__(engine_class)
    missing = [spec.name for spec in manifest.requests if engine_class.handler(engine, spec.name) is None]
    checks.append(Check("every request has a handler", not missing, ", ".join(f"on_{name}" for name in missing)))

    bus = Bus()
    bus.register(engine_class)
    try:
        health = bus.health(engine_id, timeout_s=CHECK_TIMEOUT_S)
        checks.append(Check("starts and reports health", isinstance(health, dict) and "ok" in health, str(health)))
    except EngineError as error:
        checks.append(Check("starts and reports health", False, str(error)))
        bus.shutdown()
        return checks
    try:
        bus.call(f"{engine_id}.no_such_request", timeout_s=CHECK_TIMEOUT_S)
        checks.append(Check("unknown requests are refused", False, "it answered"))
    except EngineError as error:
        checks.append(Check("unknown requests are refused", error.code == "unknown_request", error.code))

    for spec in manifest.requests:
        if spec.example is None or spec.effect != "none":
            continue
        topic = f"{engine_id}.{spec.name}"
        try:
            if spec.streams:
                items, cancelled, error = _call_stream(bus, topic, spec.example, None)
                if error:
                    raise error
                shapes_ok = all(not spec.shape or not validate(item.payload, item.attachments) for item in items)
                checks.append(Check(f"{spec.name} answers its example", shapes_ok, f"{len(items)} items"))
                _items, cancelled, error = _call_stream(bus, topic, spec.example, 1)
                checks.append(Check(f"{spec.name} can be cancelled", error is None and cancelled is not None,
                                    str(error or "")))
            else:
                reply = bus.request(topic, spec.example, timeout_s=CHECK_TIMEOUT_S).result(CHECK_TIMEOUT_S + 1)
                shape_problems = validate(reply.payload, reply.attachments) if spec.shape else []
                checks.append(Check(f"{spec.name} answers its example", not shape_problems, "; ".join(shape_problems)))
        except EngineError as error:
            checks.append(Check(f"{spec.name} answers its example", False, str(error)))

    if all(manifest.request(name) for name in SOURCE_REQUESTS):
        try:
            collections = bus.call(f"{engine_id}.collections", timeout_s=CHECK_TIMEOUT_S).get("collections")
            assert isinstance(collections, list) and all("collection" in item for item in collections), "collections"
            for item in collections[:3]:
                fields = bus.call(f"{engine_id}.schema", {"collection": item["collection"]}).get("fields")
                assert isinstance(fields, list), "schema has no fields"
                reply = bus.request(f"{engine_id}.query", {"collection": item["collection"], "query": {"limit": 5}}).result(
                    CHECK_TIMEOUT_S)
                shape_problems = validate(reply.payload, reply.attachments)
                assert reply.payload.get("shape") == "table" and not shape_problems, "; ".join(shape_problems) or "not a table"
            checks.append(Check("follows the source contract", True, f"{len(collections)} source(s)"))
        except (AssertionError, EngineError) as error:
            checks.append(Check("follows the source contract", False, str(error)))

    stopped = threading.Event()
    threading.Thread(target=lambda: (bus.shutdown(), stopped.set()), daemon=True).start()
    checks.append(Check("stops cleanly", stopped.wait(CHECK_TIMEOUT_S), ""))
    return checks


def report(checks: list[Check]) -> str:
    lines = [f"{'✔' if check.ok else '✘'} {check.name}" + (f" — {check.detail}" if check.detail else "") for check in checks]
    failed = sum(not check.ok for check in checks)
    return "\n".join(lines + [f"\n{len(checks) - failed} of {len(checks)} checks passed."])
# --- END [1.1] ---


# ==============================================================================
# [1.2] command_line
# ==============================================================================
def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print("usage: python -m engines.kernel.conformance ENGINE_FOLDER [--data DIR]", file=sys.stderr)
        return 2
    data = argv[argv.index("--data") + 1] if "--data" in argv else tempfile.mkdtemp(prefix="devos_conformance_")
    os.environ["DEVOS_DATA_DIR"] = data                   # engines open their stores here, never in your data
    checks = check_package(Path(argv[0]))
    print(report(checks))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
# --- END [1.2] ---
