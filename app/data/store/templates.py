# ==============================================================================
# FILE: app/data/store/templates.py
# OVERVIEW: The user's templates (made with "Save as template"), kept in the workspace database like pages:
#           each is a template spec as JSON. Built-in templates live in code (app/templates/builtin.py).
# PURPOSE:
#   1. TemplateStore: save (new or replace), get, list, rename, delete; ids are "user:<random>".
#   2. A template describes how a page tile is built (source kind, schema, views, layout, actions); it never
#      holds the data itself, so deleting one leaves every tile's data where it is.
# CONTRACT:
#   - Requires: app.data.store.database (Database, get_database). No Qt.
#   - Exposes: TemplateStore(database=None), get_template_store(), StoredTemplate, USER_PREFIX
#   - Tables (namespace "templates"): templates_user (template_id, title, spec_json, created_at, updated_at).
# NOTES:
#   - [2026-11-17] Created in Phase 5c of the engine plan (decision: user templates live in the workspace database).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - USER_PREFIX (str): Start of every user template id ("user:"); built-in ids have none.
# - spec (dict): A template spec as plain data (app/templates/spec.py TemplateSpec.to_dict()).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TEMPLATES
#    1.1 store
# ==============================================================================

# ==============================================================================
# [1.1] store
# ==============================================================================
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from app.data.store.database import Database, get_database

NAMESPACE = "templates"
MIGRATIONS = ["""
    CREATE TABLE templates_user (
        template_id TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        spec_json   TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    )
"""]
USER_PREFIX = "user:"


@dataclass(frozen=True)
class StoredTemplate:
    template_id: str
    title: str
    spec: dict
    created_at: float
    updated_at: float


class TemplateStore:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or get_database()
        self.database.migrate(NAMESPACE, MIGRATIONS)

    @staticmethod
    def _stored(row) -> StoredTemplate:
        return StoredTemplate(row["template_id"], row["title"], json.loads(row["spec_json"]), row["created_at"],
                              row["updated_at"])

    def templates(self) -> list[StoredTemplate]:
        return [self._stored(row) for row in self.database.fetch_all("SELECT * FROM templates_user ORDER BY title")]

    def get(self, template_id: str) -> StoredTemplate | None:
        row = self.database.fetch_one("SELECT * FROM templates_user WHERE template_id = ?", (template_id,))
        return self._stored(row) if row else None

    def save(self, title: str, spec: dict, template_id: str | None = None) -> StoredTemplate:
        """Save a spec under a new id (or replace the one given); the spec's id and title follow."""
        template_id = template_id or USER_PREFIX + uuid.uuid4().hex[:12]
        now = time.time()
        spec = {**spec, "template_id": template_id, "title": title, "is_user": True}
        self.database.execute(
            "INSERT INTO templates_user (template_id, title, spec_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(template_id) DO UPDATE SET title = excluded.title, spec_json = excluded.spec_json, "
            "updated_at = excluded.updated_at", (template_id, title, json.dumps(spec), now, now))
        return self.get(template_id)

    def rename(self, template_id: str, title: str) -> None:
        stored = self.get(template_id)
        if stored is not None and title.strip():
            self.save(title.strip(), stored.spec, template_id)

    def delete(self, template_id: str) -> None:
        """Forget a template; tiles made from it keep their data (they show that the template is gone)."""
        self.database.execute("DELETE FROM templates_user WHERE template_id = ?", (template_id,))


_stores: dict[int, TemplateStore] = {}


def get_template_store(database: Database | None = None) -> TemplateStore:
    database = database or get_database()
    if id(database) not in _stores:
        _stores[id(database)] = TemplateStore(database)
    return _stores[id(database)]
# --- END [1.1] ---
