# ==============================================================================
# FILE: app/modules/comms/engine.py
# OVERVIEW: Comms logic (no Qt): email accounts, reading mail over IMAP, sending over SMTP, a local cache
#           of message headers and bodies, passwords in the system keyring (never in SQLite), and local
#           text threads for the not-yet-connected Text channel.
# PURPOSE:
#   1. CommsStore: accounts, cached messages, text threads / messages (SQLite tables comms_*).
#   2. Passwords: keyring when available, else kept in memory for this session only.
#   3. Mail: fetch_headers(), fetch_body(), send_mail(), parse_message() (plain text preferred, HTML stripped).
# CONTRACT:
#   - Requires: app.data.store.database.Database, standard library imaplib / smtplib / email, keyring (optional).
#               No Qt.
#   - Exposes: CommsStore(database), MailAccount, MailSummary, CommsError, get_password(), set_password(),
#              password_storage(), fetch_headers(account, password, folder, limit), fetch_body(...),
#              send_mail(account, password, to, subject, body), parse_message(raw) -> (headers, body), html_to_text()
# NOTES:
#   - [2026-11-16] Created with the Comms module. Text and calls have no provider yet; their threads are local.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - account: One email identity: address, IMAP (incoming) and SMTP (outgoing) servers, user name.
# - uid (str): The IMAP UID of a message in a folder (stable while the folder's UIDVALIDITY holds).
# - KEYRING_SERVICE (str): Service name the passwords are stored under in the system keyring.
# - thread: A local conversation of the Text channel (name + messages).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STORE
#    1.1 imports_and_schema
#    1.2 comms_store
#    1.3 passwords
# 2. MAIL
#    2.1 parsing
#    2.2 imap_smtp
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_schema
# ==============================================================================
from __future__ import annotations

import email
import email.policy
import html
import imaplib
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime

from app.data.store.database import Database

KEYRING_SERVICE = "devos-comms"
MIGRATIONS = [
    """
    CREATE TABLE comms_accounts (
        account_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, address TEXT NOT NULL, username TEXT NOT NULL,
        imap_host TEXT NOT NULL, imap_port INTEGER NOT NULL DEFAULT 993,
        smtp_host TEXT NOT NULL, smtp_port INTEGER NOT NULL DEFAULT 465
    );
    CREATE TABLE comms_messages (
        account_id INTEGER NOT NULL, folder TEXT NOT NULL, uid TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', sent_at REAL NOT NULL DEFAULT 0,
        is_seen INTEGER NOT NULL DEFAULT 0, body TEXT,
        PRIMARY KEY (account_id, folder, uid)
    );
    CREATE TABLE comms_threads (
        thread_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'text'
    );
    CREATE TABLE comms_texts (
        text_id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL, is_mine INTEGER NOT NULL,
        body TEXT NOT NULL, sent_at REAL NOT NULL
    );
    """,
]


class CommsError(Exception):
    pass


@dataclass
class MailAccount:
    account_id: int
    name: str
    address: str
    username: str
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465


@dataclass
class MailSummary:
    uid: str
    sender: str
    subject: str
    sent_at: float
    is_seen: bool
# --- END [1.1] ---

# ==============================================================================
# [1.2] comms_store
# ==============================================================================
class CommsStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        database.migrate("comms", MIGRATIONS)

    def accounts(self) -> list[MailAccount]:
        return [MailAccount(row["account_id"], row["name"], row["address"], row["username"], row["imap_host"],
                            row["imap_port"], row["smtp_host"], row["smtp_port"])
                for row in self.database.fetch_all("SELECT * FROM comms_accounts ORDER BY name")]

    def account(self, account_id: int) -> MailAccount | None:
        return next((account for account in self.accounts() if account.account_id == account_id), None)

    def save_account(self, account: MailAccount) -> MailAccount:
        values = (account.name, account.address, account.username, account.imap_host, account.imap_port,
                  account.smtp_host, account.smtp_port)
        if account.account_id:
            self.database.execute("UPDATE comms_accounts SET name=?, address=?, username=?, imap_host=?, imap_port=?, "
                                  "smtp_host=?, smtp_port=? WHERE account_id=?", (*values, account.account_id))
            return account
        cursor = self.database.execute("INSERT INTO comms_accounts (name, address, username, imap_host, imap_port, "
                                       "smtp_host, smtp_port) VALUES (?, ?, ?, ?, ?, ?, ?)", values)
        return self.account(cursor.lastrowid)

    def delete_account(self, account_id: int) -> None:
        with self.database.transaction():
            self.database.execute("DELETE FROM comms_messages WHERE account_id = ?", (account_id,))
            self.database.execute("DELETE FROM comms_accounts WHERE account_id = ?", (account_id,))

    def cached_messages(self, account_id: int, folder: str = "INBOX") -> list[MailSummary]:
        rows = self.database.fetch_all("SELECT * FROM comms_messages WHERE account_id = ? AND folder = ? ORDER BY sent_at DESC",
                                       (account_id, folder))
        return [MailSummary(row["uid"], row["sender"], row["subject"], row["sent_at"], bool(row["is_seen"])) for row in rows]

    def cache_messages(self, account_id: int, folder: str, summaries: list[MailSummary]) -> None:
        with self.database.transaction():
            for summary in summaries:
                self.database.execute(
                    "INSERT INTO comms_messages (account_id, folder, uid, sender, subject, sent_at, is_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, folder, uid) DO UPDATE SET "
                    "sender = excluded.sender, subject = excluded.subject, sent_at = excluded.sent_at, is_seen = excluded.is_seen",
                    (account_id, folder, summary.uid, summary.sender, summary.subject, summary.sent_at, int(summary.is_seen)))

    def cached_body(self, account_id: int, folder: str, uid: str) -> str | None:
        row = self.database.fetch_one("SELECT body FROM comms_messages WHERE account_id = ? AND folder = ? AND uid = ?",
                                      (account_id, folder, uid))
        return row["body"] if row else None

    def cache_body(self, account_id: int, folder: str, uid: str, body: str) -> None:
        self.database.execute("UPDATE comms_messages SET body = ?, is_seen = 1 WHERE account_id = ? AND folder = ? AND uid = ?",
                              (body, account_id, folder, uid))

    def threads(self) -> list[tuple[int, str]]:
        return [(row["thread_id"], row["name"]) for row in self.database.fetch_all("SELECT * FROM comms_threads ORDER BY name")]

    def add_thread(self, name: str) -> int:
        return self.database.execute("INSERT INTO comms_threads (name) VALUES (?)", (name,)).lastrowid

    def texts(self, thread_id: int) -> list[tuple[bool, str, float]]:
        return [(bool(row["is_mine"]), row["body"], row["sent_at"]) for row in
                self.database.fetch_all("SELECT * FROM comms_texts WHERE thread_id = ? ORDER BY sent_at", (thread_id,))]

    def add_text(self, thread_id: int, body: str, is_mine: bool = True) -> None:
        self.database.execute("INSERT INTO comms_texts (thread_id, is_mine, body, sent_at) VALUES (?, ?, ?, ?)",
                              (thread_id, int(is_mine), body, time.time()))
# --- END [1.2] ---

# ==============================================================================
# [1.3] passwords
# ==============================================================================
_session_passwords: dict[int, str] = {}


def _keyring():
    try:
        import keyring
        keyring.get_keyring()
        return keyring
    except Exception:                                  # no package, or no usable backend (no Secret Service)
        return None


def password_storage() -> str:
    ring = _keyring()
    if ring is None:
        return "this session only (no system keyring)"
    name = type(ring.get_keyring()).__name__
    return "nowhere (no keyring backend)" if "fail" in name.lower() else f"system keyring ({name})"


def set_password(account_id: int, password: str, remember: bool = True) -> None:
    _session_passwords[account_id] = password
    ring = _keyring()
    if remember and ring is not None:
        try:
            ring.set_password(KEYRING_SERVICE, str(account_id), password)
        except Exception:                              # keyring locked / unavailable: session memory still works
            pass


def get_password(account_id: int) -> str | None:
    if account_id in _session_passwords:
        return _session_passwords[account_id]
    ring = _keyring()
    if ring is not None:
        try:
            return ring.get_password(KEYRING_SERVICE, str(account_id))
        except Exception:
            return None
    return None


def forget_password(account_id: int) -> None:
    _session_passwords.pop(account_id, None)
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(KEYRING_SERVICE, str(account_id))
        except Exception:
            pass
# --- END [1.3] ---


# ==============================================================================
# [2.1] parsing
# ==============================================================================
def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def parse_message(raw: bytes) -> tuple[dict, str]:
    """(headers {from, to, subject, date}, readable body) from a raw RFC 822 message."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    headers = {key: str(message.get(key, "")) for key in ("from", "to", "cc", "subject", "date")}
    body = ""
    part = message.get_body(preferencelist=("plain", "html")) if message.is_multipart() or message.get_content_type().startswith("text/") else None
    if part is not None:
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            content = part.get_payload(decode=True).decode("utf-8", errors="replace")
        body = html_to_text(content) if part.get_content_type() == "text/html" else content
    attachments = [part.get_filename() for part in message.iter_attachments()] if message.is_multipart() else []
    if attachments:
        body += "\n\n📎 " + ", ".join(name or "attachment" for name in attachments)
    return headers, body.strip()


def _summary(uid: str, header_bytes: bytes, flags: bytes) -> MailSummary:
    message = email.message_from_bytes(header_bytes, policy=email.policy.default)
    try:
        sent_at = parsedate_to_datetime(str(message.get("date"))).timestamp()
    except (TypeError, ValueError):
        sent_at = 0.0
    return MailSummary(uid, str(message.get("from", "")), str(message.get("subject", "")) or "(no subject)", sent_at,
                       b"\\Seen" in flags)
# --- END [2.1] ---

# ==============================================================================
# [2.2] imap_smtp
# ==============================================================================
def _imap(account: MailAccount, password: str) -> imaplib.IMAP4:
    try:
        connection = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, ssl_context=ssl.create_default_context(), timeout=20)
        connection.login(account.username, password)
        return connection
    except (imaplib.IMAP4.error, OSError) as error:
        raise CommsError(f"IMAP {account.imap_host}: {error}") from error


def fetch_headers(account: MailAccount, password: str, folder: str = "INBOX", limit: int = 50) -> list[MailSummary]:
    """The newest `limit` messages of a folder (headers and seen flag only)."""
    connection = _imap(account, password)
    try:
        status, _data = connection.select(folder, readonly=True)
        if status != "OK":
            raise CommsError(f"can't open folder {folder}")
        status, data = connection.uid("search", None, "ALL")
        uids = data[0].split()[-limit:] if status == "OK" and data and data[0] else []
        if not uids:
            return []
        status, data = connection.uid("fetch", b",".join(uids), "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        summaries = []
        for entry in data:
            if isinstance(entry, tuple):
                meta, header_bytes = entry
                match = re.search(rb"UID (\d+)", meta)
                if match:
                    summaries.append(_summary(match.group(1).decode(), header_bytes, meta))
        return sorted(summaries, key=lambda summary: summary.sent_at, reverse=True)
    except imaplib.IMAP4.error as error:
        raise CommsError(f"IMAP: {error}") from error
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def fetch_body(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> tuple[dict, str]:
    connection = _imap(account, password)
    try:
        connection.select(folder, readonly=True)
        status, data = connection.uid("fetch", uid.encode(), "(BODY.PEEK[])")
        raw = next((entry[1] for entry in data or [] if isinstance(entry, tuple)), None)
        if status != "OK" or raw is None:
            raise CommsError(f"message {uid} not found")
        return parse_message(raw)
    except imaplib.IMAP4.error as error:
        raise CommsError(f"IMAP: {error}") from error
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def build_message(account: MailAccount, to: str, subject: str, body: str) -> EmailMessage:
    recipients = [part.strip() for part in re.split(r"[,;]", to) if part.strip()]
    if not recipients or any("@" not in recipient for recipient in recipients):
        raise CommsError("add at least one valid recipient address")
    message = EmailMessage()
    message["From"] = f"{account.name} <{account.address}>" if account.name else account.address
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = format_datetime(email.utils.localtime())
    message.set_content(body)
    return message


def send_mail(account: MailAccount, password: str, to: str, subject: str, body: str) -> None:
    message = build_message(account, to, subject, body)
    context = ssl.create_default_context()
    try:
        if account.smtp_port == 465:
            with smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, context=context, timeout=30) as server:
                server.login(account.username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(account.username, password)
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        raise CommsError(f"SMTP {account.smtp_host}: {error}") from error
# --- END [2.2] ---
