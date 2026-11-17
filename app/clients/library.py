# ==============================================================================
# FILE: app/clients/library.py
# OVERVIEW: The Library client - how modules and tools store and find the user's files: a small, blocking
#           wrapper over the Knowledge engine's requests (the engine owns <data>/library/ and its index).
# PURPOSE:
#   1. library() -> LibraryClient with the familiar calls: items, get, create_text, add_file, add_bytes,
#      read_text, write_text, rename, delete, export, path, new_path + register_path, unique_name, refresh_size.
#      Each is one quick request over the bus (local, a few ms); failures raise LibraryError with a message
#      the user can act on.
#   2. LibraryItem: an item as plain data (no file paths: ask path() when a tool must open the file itself).
#   3. Changes: watch_library(owner, callback(kind)) in app/ui/engine_bridge.py ("knowledge.changed").
#   For big or slow work, use call_engine("knowledge.…") instead (never block the UI for long).
# CONTRACT:
#   - Requires: engines.runtime.get_bus, engines.kernel.EngineError. No Qt.
#   - Exposes: library() -> LibraryClient, LibraryItem, LibraryError
# NOTES:
#   - [2026-11-17] Created in Phase 4 of the engine plan (replaces app.data.store.content.get_content_store()).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - TIMEOUT_S (float): Longest a Library call waits (default: 30; copying big files takes a while).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LIBRARY_CLIENT
#    1.1 item
#    1.2 client
# ==============================================================================

# ==============================================================================
# [1.1] item
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from engines.kernel import EngineError

TIMEOUT_S = 30.0


class LibraryError(Exception):
    """A Library request failed; the message says why."""


@dataclass(frozen=True)
class LibraryItem:
    item_id: int
    kind: str
    name: str
    extension: str
    size_bytes: int
    created_at: float
    updated_at: float
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def from_payload(data: dict) -> "LibraryItem":
        return LibraryItem(int(data["item_id"]), data["kind"], data["name"], data.get("extension", ""),
                           int(data.get("size_bytes", 0)), float(data.get("created_at", 0)),
                           float(data.get("updated_at", 0)), dict(data.get("metadata") or {}))
# --- END [1.1] ---


# ==============================================================================
# [1.2] client
# ==============================================================================
class LibraryClient:
    def _call(self, name: str, payload: dict | None = None, attachments: dict | None = None) -> dict:
        from engines.runtime import get_bus
        try:
            return get_bus().request(f"knowledge.{name}", payload or {}, attachments,
                                     timeout_s=TIMEOUT_S).result().payload
        except EngineError as error:
            raise LibraryError(str(error)) from None

    def items(self, kind: str | None = None) -> list[LibraryItem]:
        return [LibraryItem.from_payload(item) for item in self._call("list", {"kind": kind} if kind else {})["items"]]

    def get(self, item_id: int) -> LibraryItem | None:
        try:
            return LibraryItem.from_payload(self._call("item", {"item_id": int(item_id)})["item"])
        except LibraryError:
            return None

    def search(self, text: str, kind: str | None = None, limit: int = 200) -> list[LibraryItem]:
        payload = {"text": text, "limit": limit} | ({"kind": kind} if kind else {})
        return [LibraryItem.from_payload(item) for item in self._call("search", payload)["items"]]

    def create_text(self, kind: str, name: str, text: str = "", extension: str | None = None,
                    metadata: dict | None = None) -> LibraryItem:
        payload = {"kind": kind, "name": name, "text": text} | ({"extension": extension} if extension else {}) | \
            ({"metadata": metadata} if metadata else {})
        return LibraryItem.from_payload(self._call("create_text", payload)["item"])

    def add_file(self, kind: str, source_path: str | Path, name: str | None = None, metadata: dict | None = None) -> LibraryItem:
        payload = {"kind": kind, "source_path": str(Path(source_path).resolve())} | ({"name": name} if name else {}) | \
            ({"metadata": metadata} if metadata else {})
        return LibraryItem.from_payload(self._call("add_file", payload)["item"])

    def add_bytes(self, kind: str, name: str, data: bytes, extension: str, metadata: dict | None = None) -> LibraryItem:
        payload = {"kind": kind, "name": name, "extension": extension} | ({"metadata": metadata} if metadata else {})
        return LibraryItem.from_payload(self._call("add_bytes", payload, {"data": bytes(data)})["item"])

    def read_text(self, item_id: int) -> str:
        return self._call("read_text", {"item_id": int(item_id)})["text"]

    def write_text(self, item_id: int, text: str) -> None:
        self._call("write_text", {"item_id": int(item_id), "text": text})

    def rename(self, item_id: int, name: str) -> str:
        return self._call("rename", {"item_id": int(item_id), "name": name})["name"]

    def delete(self, item_id: int) -> None:
        self._call("delete", {"ids": [int(item_id)]})

    def export(self, item_id: int, destination: str | Path) -> Path:
        return Path(self._call("export", {"item_id": int(item_id), "destination": str(destination)})["path"])

    def path(self, item: LibraryItem | int) -> Path:
        """The item's file on this machine - for tools that open it with their own library (SQLite, Kùzu ...)."""
        item_id = item.item_id if isinstance(item, LibraryItem) else int(item)
        return Path(self._call("path", {"item_id": item_id})["path"])

    def new_path(self, kind: str, extension: str) -> Path:
        """A fresh Library path for a tool that writes the file itself; then register_path()."""
        return Path(self._call("new_path", {"kind": kind, "extension": extension})["path"])

    def register_path(self, kind: str, name: str, path: str | Path, metadata: dict | None = None) -> LibraryItem:
        payload = {"kind": kind, "name": name, "path": str(path)} | ({"metadata": metadata} if metadata else {})
        return LibraryItem.from_payload(self._call("register_path", payload)["item"])

    def unique_name(self, kind: str, base: str) -> str:
        return self._call("unique_name", {"kind": kind, "base": base})["name"]

    def refresh_size(self, item_id: int) -> None:
        self._call("refresh_size", {"item_id": int(item_id)})


_client = LibraryClient()


def library() -> LibraryClient:
    return _client
# --- END [1.2] ---
