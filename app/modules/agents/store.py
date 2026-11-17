# ==============================================================================
# FILE: app/modules/agents/store.py
# OVERVIEW: Agent chats on SQLite - every agent has conversations, every conversation has messages.
# PURPOSE:
#   1. ChatStore: list / start / rename / delete conversations of an agent; read and add messages.
#   2. Deleting an agent keeps its chats (user data is never deleted silently); only an explicit
#      "Delete chat" removes one.
# CONTRACT:
#   - Requires: app.data.store.database.Database. No Qt.
#   - Exposes: ChatStore(database), get_chat_store(database=None) (one per database), Conversation,
#              StoredMessage, conversation_title(text)
#   - Tables (namespace "agents"): agents_conversations, agents_messages.
# NOTES:
#   - [2026-11-17] Created with the Agents chat view.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - conversation: One chat with an agent (title + messages, newest conversation first).
# - stored message: One message of a conversation: role ("user" / "assistant"), text, the model that answered.
# - TITLE_CHARS (int): A new conversation is titled after its first message, cut to this length.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 schema_and_types
#    1.2 chat_store
# ==============================================================================

# ==============================================================================
# [1.1] schema_and_types
# ==============================================================================
from __future__ import annotations

import time
from dataclasses import dataclass

from app.data.store.database import Database, get_database

TITLE_CHARS = 48
MIGRATIONS = [
    """
    CREATE TABLE agents_conversations (
        conversation_id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER NOT NULL,
        title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX agents_conversations_by_agent ON agents_conversations (agent_id, updated_at);
    CREATE TABLE agents_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
    );
    CREATE INDEX agents_messages_by_conversation ON agents_messages (conversation_id, message_id)
    """,
]


@dataclass(frozen=True)
class Conversation:
    conversation_id: int
    agent_id: int
    title: str
    updated_at: float


@dataclass(frozen=True)
class StoredMessage:
    message_id: int
    role: str
    content: str
    model: str
    created_at: float


def conversation_title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_CHARS else line[:TITLE_CHARS - 1].rstrip() + "…"
# --- END [1.1] ---


# ==============================================================================
# [1.2] chat_store
# ==============================================================================
class ChatStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        database.migrate("agents", MIGRATIONS)

    def conversations(self, agent_id: int) -> list[Conversation]:
        rows = self.database.fetch_all("SELECT * FROM agents_conversations WHERE agent_id = ? "
                                       "ORDER BY updated_at DESC, conversation_id DESC", (agent_id,))
        return [Conversation(row["conversation_id"], row["agent_id"], row["title"], row["updated_at"]) for row in rows]

    def new_conversation(self, agent_id: int, title: str = "New chat") -> Conversation:
        now = time.time()
        cursor = self.database.execute("INSERT INTO agents_conversations (agent_id, title, created_at, updated_at) "
                                       "VALUES (?, ?, ?, ?)", (agent_id, title, now, now))
        return Conversation(cursor.lastrowid, agent_id, title, now)

    def rename_conversation(self, conversation_id: int, title: str) -> None:
        self.database.execute("UPDATE agents_conversations SET title = ? WHERE conversation_id = ?", (title, conversation_id))

    def delete_conversation(self, conversation_id: int) -> None:
        with self.database.transaction():
            self.database.execute("DELETE FROM agents_messages WHERE conversation_id = ?", (conversation_id,))
            self.database.execute("DELETE FROM agents_conversations WHERE conversation_id = ?", (conversation_id,))

    def messages(self, conversation_id: int) -> list[StoredMessage]:
        rows = self.database.fetch_all("SELECT * FROM agents_messages WHERE conversation_id = ? ORDER BY message_id",
                                       (conversation_id,))
        return [StoredMessage(row["message_id"], row["role"], row["content"], row["model"], row["created_at"]) for row in rows]

    def add_message(self, conversation_id: int, role: str, content: str, model: str = "") -> int:
        now = time.time()
        with self.database.transaction():
            cursor = self.database.execute("INSERT INTO agents_messages (conversation_id, role, content, model, created_at) "
                                           "VALUES (?, ?, ?, ?, ?)", (conversation_id, role, content, model, now))
            self.database.execute("UPDATE agents_conversations SET updated_at = ? WHERE conversation_id = ?", (now, conversation_id))
        return cursor.lastrowid

    def delete_message(self, message_id: int) -> None:
        self.database.execute("DELETE FROM agents_messages WHERE message_id = ?", (message_id,))


_stores: dict[int, ChatStore] = {}


def get_chat_store(database: Database | None = None) -> ChatStore:
    """One ChatStore per database (migrated once), shared by every chat view."""
    database = database or get_database()
    if id(database) not in _stores or _stores[id(database)].database is not database:
        _stores[id(database)] = ChatStore(database)
    return _stores[id(database)]
# --- END [1.2] ---
