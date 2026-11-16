# ==============================================================================
# FILE: app/modules/comms/view.py
# OVERVIEW: Comms module - email (IMAP / SMTP) working today; text messages and calls as placeholders
#           with their UI in place until a provider is chosen. Thunderbird opens from here if installed.
# PURPOSE:
#   1. Blocks: Accounts (servers, user, password in the keyring, test), Inbox (cached + refresh), Reader
#      (message, reply), Compose (send), Text (local threads; no provider yet), Calls (dial pad; no VoIP yet).
#   2. Network work runs off the UI thread (ui_tools.run_in_thread); every outcome shows in the status line.
# CONTRACT:
#   - Requires: app.modules.base.ModuleView, app.modules.comms.engine, app.ui (ui_tools, tool_bag, canvas),
#               app.data.activity_log, QSS selectors #comms_panel, #comms_banner_label, #mail_body_view,
#               #dial_btn
#   - Exposes: CommsModule(parent=None, context=None) with store, account_id, selected_uid, refresh_inbox(),
#              open_message(uid), send(to, subject, body), compose_reply(); AccountsBlock, InboxBlock,
#              ReaderBlock, ComposeBlock, TextBlock, CallsBlock
# NOTES:
#   - [2026-11-16] Created (Comms: email first; text / VoIP placeholders).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - placeholder channel: A block whose UI works locally but isn't connected to a provider yet (Text, Calls).
# - FOLDER (str): The mail folder shown ("INBOX").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SHARED
#    1.1 imports
# 2. MAIL_BLOCKS
#    2.1 accounts_block
#    2.2 inbox_block
#    2.3 reader_block
#    2.4 compose_block
# 3. PLACEHOLDER_BLOCKS
#    3.1 text_block
#    3.2 calls_block
# 4. MODULE
#    4.1 comms_module
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

import datetime as dt
import shutil
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.data.activity_log import get_activity_log
from app.data.store.database import get_database
from app.modules.base import ModuleView
from app.modules.comms.engine import (
    CommsStore, MailAccount, fetch_body, fetch_headers, forget_password, get_password, password_storage,
    send_mail, set_password,
)
from app.ui.canvas.block_canvas import BlockSpec
from app.ui.canvas.tile_canvas import reserve_controls_space
from app.ui.tool_bag import ToolSpec, register_tools
from app.ui.ui_tools import add_button, block_layout, control_label, run_in_thread, set_status, status_label

FOLDER = "INBOX"
DEFAULT_BLOCKS = {"split": "h", "sizes": [2, 3], "children": [
    {"split": "v", "sizes": [3, 2], "children": [{"kind": "inbox"}, {"kind": "accounts"}]},
    {"split": "v", "sizes": [3, 2], "children": [{"kind": "reader"}, {"kind": "compose"}]},
]}


def banner(text: str) -> QLabel:
    return QLabel(text, objectName="comms_banner_label", wordWrap=True)
# --- END [1.1] ---


# ==============================================================================
# [2.1] accounts_block
# ==============================================================================
class AccountsBlock(QWidget):
    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.account_combo = QComboBox()
        self.account_combo.activated.connect(lambda: self._load(self.account_combo.currentData()))
        row.addWidget(self.account_combo, 1)
        add_button(row, "+ Account", lambda: self._load(None))
        reserve_controls_space(row)
        form = QFormLayout()
        self.fields = {key: QLineEdit(placeholderText=hint) for key, hint in (
            ("name", "Your name"), ("address", "you@example.com"), ("username", "login (often the address)"),
            ("imap_host", "imap.example.com"), ("smtp_host", "smtp.example.com"))}
        self.imap_port, self.smtp_port = QSpinBox(maximum=65535, value=993), QSpinBox(maximum=65535, value=465)
        self.password_edit = QLineEdit(echoMode=QLineEdit.EchoMode.Password, placeholderText="app password")
        self.remember_check = QCheckBox("Remember in the system keyring", checked=True)
        for key, title in (("name", "Name"), ("address", "Address"), ("username", "User")):
            form.addRow(title, self.fields[key])
        for title, host, port in (("IMAP (in)", "imap_host", self.imap_port), ("SMTP (out)", "smtp_host", self.smtp_port)):
            line = QHBoxLayout()
            line.addWidget(self.fields[host], 1)
            line.addWidget(port)
            form.addRow(title, line)
        form.addRow("Password", self.password_edit)
        form.addRow("", self.remember_check)
        self.fields["address"].editingFinished.connect(self._suggest_servers)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        add_button(buttons, "Save", self._save)
        add_button(buttons, "Test", self._test, "Log in to the IMAP server")
        add_button(buttons, "Delete", self._delete)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(control_label(f"Passwords are kept in: {password_storage()} — never in the DevOS database. "
                                       "Gmail / Outlook need an app password."))
        layout.addStretch()
        module.accounts_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.account_combo.clear()
        for account in self.module.store.accounts():
            self.account_combo.addItem(f"{account.name or account.address}", account.account_id)
        self.account_combo.setCurrentIndex(max(0, self.account_combo.findData(self.module.account_id)))
        self._load(self.account_combo.currentData())

    def _load(self, account_id: int | None) -> None:
        account = self.module.store.account(account_id) if account_id else None
        self.editing_id = account.account_id if account else 0
        for key, field in self.fields.items():
            field.setText(getattr(account, key) if account else "")
        self.imap_port.setValue(account.imap_port if account else 993)
        self.smtp_port.setValue(account.smtp_port if account else 465)
        self.password_edit.clear()
        self.password_edit.setPlaceholderText("saved — type to change" if account and get_password(account.account_id) else "app password")
        if account:
            self.module.set_account(account.account_id)

    def _suggest_servers(self) -> None:
        domain = self.fields["address"].text().partition("@")[2].strip()
        if domain:
            for key, prefix in (("imap_host", "imap"), ("smtp_host", "smtp")):
                if not self.fields[key].text():
                    self.fields[key].setText(f"{prefix}.{domain}")
            if not self.fields["username"].text():
                self.fields["username"].setText(self.fields["address"].text().strip())

    def _account(self) -> MailAccount:
        return MailAccount(self.editing_id, self.fields["name"].text().strip(), self.fields["address"].text().strip(),
                           self.fields["username"].text().strip(), self.fields["imap_host"].text().strip(),
                           self.imap_port.value(), self.fields["smtp_host"].text().strip(), self.smtp_port.value())

    def _save(self) -> MailAccount | None:
        account = self._account()
        if "@" not in account.address or not account.imap_host or not account.smtp_host or not account.username:
            self.module.report("An account needs an address, a user and both servers", is_error=True)
            return None
        account = self.module.store.save_account(account)
        if self.password_edit.text():
            set_password(account.account_id, self.password_edit.text(), self.remember_check.isChecked())
        self.module.log.info("comms", f"Saved account {account.address}")
        self.module.set_account(account.account_id)
        self.module.accounts_changed.emit()
        self.module.report(f"Saved {account.address}")
        return account

    def _test(self) -> None:
        account = self._save()
        if account is not None:
            self.module.refresh_inbox()

    def _delete(self) -> None:
        if not self.editing_id:
            return
        account = self.module.store.account(self.editing_id)
        if QMessageBox.question(self, "Delete account", f"Remove {account.address} and its cached mail from DevOS?\n"
                                "(Nothing is deleted on the mail server.)") == QMessageBox.StandardButton.Yes:
            self.module.store.delete_account(account.account_id)
            forget_password(account.account_id)
            self.module.log.info("comms", f"Removed account {account.address}")
            self.module.set_account(None)
            self.module.accounts_changed.emit()
# --- END [2.1] ---

# ==============================================================================
# [2.2] inbox_block
# ==============================================================================
class InboxBlock(QWidget):
    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.filter_edit = QLineEdit(placeholderText="Filter by sender or subject", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self.refresh)
        row.addWidget(self.filter_edit, 1)
        add_button(row, "⟳ Refresh", module.refresh_inbox)
        reserve_controls_space(row)
        self.message_tree = QTreeWidget(objectName="message_tree", rootIsDecorated=False, uniformRowHeights=True)
        self.message_tree.setHeaderLabels(["From", "Subject", "Date"])
        self.message_tree.setColumnWidth(0, 180)
        self.message_tree.setColumnWidth(1, 300)
        self.message_tree.itemClicked.connect(lambda item: module.open_message(item.data(0, Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.message_tree, 1)
        self.empty_label = control_label()
        layout.addWidget(self.empty_label)
        module.inbox_changed.connect(self.refresh)
        module.account_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.message_tree.clear()
        if self.module.account_id is None:
            self.empty_label.setText("Add an email account to see your inbox.")
            return
        needle = self.filter_edit.text().strip().lower()
        messages = [message for message in self.module.store.cached_messages(self.module.account_id, FOLDER)
                    if not needle or needle in f"{message.sender} {message.subject}".lower()]
        bold = QFont()
        bold.setBold(True)
        for message in messages:
            when = dt.datetime.fromtimestamp(message.sent_at).strftime("%Y-%m-%d %H:%M") if message.sent_at else ""
            item = QTreeWidgetItem([message.sender, message.subject, when])
            item.setData(0, Qt.ItemDataRole.UserRole, message.uid)
            if not message.is_seen:
                for column in range(3):
                    item.setFont(column, bold)
            self.message_tree.addTopLevelItem(item)
            if message.uid == self.module.selected_uid:
                self.message_tree.setCurrentItem(item)
        self.empty_label.setText("" if messages else "No messages cached yet — press Refresh.")
# --- END [2.2] ---

# ==============================================================================
# [2.3] reader_block
# ==============================================================================
class ReaderBlock(QWidget):
    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        self.subject_label = QLabel("No message open", objectName="structure_title_label", wordWrap=True)
        row.addWidget(self.subject_label, 1)
        add_button(row, "Reply", module.compose_reply)
        reserve_controls_space(row)
        self.meta_label = control_label()
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)
        self.mail_body_view = QTextBrowser(objectName="mail_body_view", openExternalLinks=True)
        layout.addWidget(self.mail_body_view, 1)
        module.message_opened.connect(self.refresh)

    def refresh(self) -> None:
        headers, body = self.module.open_headers, self.module.open_body
        self.subject_label.setText(headers.get("subject") or "No message open")
        self.meta_label.setText(f"From {headers.get('from', '')}  ·  to {headers.get('to', '')}  ·  {headers.get('date', '')}"
                                if headers else "")
        self.mail_body_view.setPlainText(body)
# --- END [2.3] ---

# ==============================================================================
# [2.4] compose_block
# ==============================================================================
class ComposeBlock(QWidget):
    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("New message"))
        row.addStretch()
        self.send_btn = add_button(row, "Send", self._send)
        reserve_controls_space(row)
        form = QFormLayout()
        self.to_edit = QLineEdit(placeholderText="name@example.com, …")
        self.subject_edit = QLineEdit()
        form.addRow("To", self.to_edit)
        form.addRow("Subject", self.subject_edit)
        layout.addLayout(form)
        self.body_edit = QPlainTextEdit()
        layout.addWidget(self.body_edit, 1)
        module.reply_requested.connect(self._fill)
        module.sent.connect(self._clear)

    def _fill(self, to: str, subject: str, quoted: str) -> None:
        self.to_edit.setText(to)
        self.subject_edit.setText(subject)
        self.body_edit.setPlainText(quoted)
        self.body_edit.setFocus()

    def _send(self) -> None:
        self.module.send(self.to_edit.text(), self.subject_edit.text(), self.body_edit.toPlainText())

    def _clear(self) -> None:
        for field in (self.to_edit, self.subject_edit):
            field.clear()
        self.body_edit.clear()

    def block_state(self) -> dict:
        return {"to": self.to_edit.text(), "subject": self.subject_edit.text(), "body": self.body_edit.toPlainText()}

    def restore_block_state(self, state: dict) -> None:
        self._fill(state.get("to", ""), state.get("subject", ""), state.get("body", ""))
# --- END [2.4] ---


# ==============================================================================
# [3.1] text_block
# ==============================================================================
class TextBlock(QWidget):
    """Conversations UI; no SMS / chat provider is connected yet, so messages stay on this computer."""

    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        self.thread_id: int | None = None
        layout, row = block_layout(self)
        self.thread_combo = QComboBox()
        self.thread_combo.activated.connect(lambda: self._open(self.thread_combo.currentData()))
        row.addWidget(self.thread_combo, 1)
        add_button(row, "+ Conversation", self._new_thread)
        reserve_controls_space(row)
        layout.addWidget(banner("Text messaging isn't connected to a provider yet (SMS gateway, Signal, Matrix …). "
                                "Conversations here are kept on this computer."))
        self.texts_list = QListWidget(objectName="texts_list", wordWrap=True)
        layout.addWidget(self.texts_list, 1)
        line = QHBoxLayout()
        self.text_edit = QLineEdit(placeholderText="Message")
        self.text_edit.returnPressed.connect(self._send)
        line.addWidget(self.text_edit, 1)
        add_button(line, "Send", self._send)
        layout.addLayout(line)
        self._fill_threads()

    def _fill_threads(self) -> None:
        self.thread_combo.clear()
        for thread_id, name in self.module.store.threads():
            self.thread_combo.addItem(name, thread_id)
        if self.thread_combo.count():
            self._open(self.thread_combo.currentData())

    def _new_thread(self) -> None:
        name, is_ok = QInputDialog.getText(self, "New conversation", "With (name or number)")
        if is_ok and name.strip():
            self.thread_id = self.module.store.add_thread(name.strip())
            self._fill_threads()
            self.thread_combo.setCurrentIndex(self.thread_combo.findData(self.thread_id))
            self._open(self.thread_id)

    def _open(self, thread_id: int | None) -> None:
        self.thread_id = thread_id
        self.texts_list.clear()
        for is_mine, body, sent_at in self.module.store.texts(thread_id) if thread_id else []:
            item = QListWidgetItem(f"{body}\n{dt.datetime.fromtimestamp(sent_at):%H:%M} · not sent (no provider)")
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight if is_mine else Qt.AlignmentFlag.AlignLeft)
            self.texts_list.addItem(item)
        self.texts_list.scrollToBottom()

    def _send(self) -> None:
        if self.thread_id is None:
            self._new_thread()
        if self.thread_id is not None and self.text_edit.text().strip():
            self.module.store.add_text(self.thread_id, self.text_edit.text().strip())
            self.text_edit.clear()
            self._open(self.thread_id)
# --- END [3.1] ---

# ==============================================================================
# [3.2] calls_block
# ==============================================================================
class CallsBlock(QWidget):
    def __init__(self, module: "CommsModule") -> None:
        super().__init__()
        self.module = module
        layout, row = block_layout(self)
        row.addWidget(control_label("Calls"))
        row.addStretch()
        reserve_controls_space(row)
        layout.addWidget(banner("Voice calls (VoIP / SIP) aren't connected yet. The dial pad is ready for when a "
                                "provider is chosen."))
        self.number_edit = QLineEdit(placeholderText="Number or SIP address", alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.number_edit)
        grid = QGridLayout()
        for index, key in enumerate("123456789*0#"):
            button = QPushButton(key, objectName="dial_btn")
            button.clicked.connect(lambda _checked=False, key=key: self.number_edit.insert(key))
            grid.addWidget(button, index // 3, index % 3)
        layout.addLayout(grid)
        call_btn = QPushButton("📞 Call")
        call_btn.clicked.connect(lambda: module.report("Calling needs a VoIP provider — not connected yet", is_error=True))
        layout.addWidget(call_btn)
        layout.addStretch()
# --- END [3.2] ---


# ==============================================================================
# [4.1] comms_module
# ==============================================================================
class CommsModule(ModuleView):
    accounts_changed = Signal()
    account_changed = Signal()
    inbox_changed = Signal()
    message_opened = Signal()
    reply_requested = Signal(str, str, str)
    sent = Signal()

    def __init__(self, parent: QWidget | None = None, context=None) -> None:
        super().__init__(parent, context, "comms_panel")
        self.log = get_activity_log()
        self.store = CommsStore(context.database if context is not None else get_database())
        accounts = self.store.accounts()
        saved = self.setting("account_id")
        self.account_id = saved if any(account.account_id == saved for account in accounts) else (accounts[0].account_id if accounts else None)
        self.selected_uid: str | None = None
        self.open_headers: dict = {}
        self.open_body = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        thunderbird = shutil.which("thunderbird")
        if thunderbird:
            tb_btn = QPushButton("Open Thunderbird")
            tb_btn.clicked.connect(lambda: subprocess.Popen([thunderbird], start_new_session=True))
            row.addWidget(tb_btn)
        self.module_status_label = status_label()
        row.addWidget(self.module_status_label, 1)
        reserve_controls_space(row)
        layout.addLayout(row)
        self.block_canvas = self.build_block_canvas([
            BlockSpec("inbox", "Inbox", "Messages of the selected account (cached; refresh to fetch)", lambda: InboxBlock(self)),
            BlockSpec("reader", "Reader", "The open message; reply", lambda: ReaderBlock(self)),
            BlockSpec("compose", "Compose", "Write and send an email", lambda: ComposeBlock(self)),
            BlockSpec("accounts", "Accounts", "Email accounts: servers, user, password (system keyring)", lambda: AccountsBlock(self)),
            BlockSpec("text", "Text", "Conversations (placeholder: no SMS / chat provider yet)", lambda: TextBlock(self)),
            BlockSpec("calls", "Calls", "Dial pad (placeholder: no VoIP provider yet)", lambda: CallsBlock(self)),
        ], DEFAULT_BLOCKS)
        layout.addWidget(self.block_canvas, 1)
        self.report("Add an account to start" if self.account_id is None else "Press Refresh in the Inbox to fetch mail")

    def report(self, text: str, is_error: bool = False) -> None:
        if is_error:
            self.log.warning("comms", text)
        set_status(self.module_status_label, text, is_error)

    def set_account(self, account_id: int | None) -> None:
        if account_id != self.account_id:
            self.account_id = account_id
            self.save_setting("account_id", account_id)
            self.account_changed.emit()

    def _account_and_password(self) -> tuple[MailAccount, str] | None:
        account = self.store.account(self.account_id) if self.account_id else None
        if account is None:
            self.report("Add an email account first (Accounts block)", is_error=True)
            return None
        password = get_password(account.account_id)
        if not password:
            password, is_ok = QInputDialog.getText(self, "Password", f"Password for {account.username}",
                                                   QLineEdit.EchoMode.Password)
            if not is_ok or not password:
                return None
            set_password(account.account_id, password, remember=False)
        return account, password

    def refresh_inbox(self) -> None:
        credentials = self._account_and_password()
        if credentials is None:
            return
        account, password = credentials
        self.report(f"Fetching {account.address}…")

        def done(summaries, error) -> None:
            if error is not None:
                self.report(f"Couldn't fetch mail: {error}", is_error=True)
                return
            self.store.cache_messages(account.account_id, FOLDER, summaries)
            self.log.success("comms", f"Fetched {len(summaries)} messages for {account.address}")
            self.report(f"{len(summaries)} messages · {dt.datetime.now():%H:%M}")
            self.inbox_changed.emit()
        run_in_thread(self, lambda: fetch_headers(account, password, FOLDER), done)

    def open_message(self, uid: str) -> None:
        self.selected_uid = uid
        cached = self.store.cached_body(self.account_id, FOLDER, uid)
        summary = next((message for message in self.store.cached_messages(self.account_id, FOLDER) if message.uid == uid), None)
        if cached is not None and summary is not None:
            self.open_headers = {"from": summary.sender, "subject": summary.subject,
                                 "date": dt.datetime.fromtimestamp(summary.sent_at).strftime("%Y-%m-%d %H:%M")}
            self.open_body = cached
            self.message_opened.emit()
            return
        credentials = self._account_and_password()
        if credentials is None:
            return
        account, password = credentials

        def done(result, error) -> None:
            if error is not None:
                self.report(f"Couldn't open the message: {error}", is_error=True)
                return
            self.open_headers, self.open_body = result
            self.store.cache_body(account.account_id, FOLDER, uid, self.open_body)
            self.message_opened.emit()
            self.inbox_changed.emit()
        run_in_thread(self, lambda: fetch_body(account, password, uid, FOLDER), done)

    def compose_reply(self) -> None:
        if not self.open_headers:
            return
        subject = self.open_headers.get("subject", "")
        quoted = "\n\n" + "\n".join(f"> {line}" for line in self.open_body.splitlines()[:200])
        self.reply_requested.emit(self.open_headers.get("from", ""), subject if subject.lower().startswith("re:") else f"Re: {subject}", quoted)

    def send(self, to: str, subject: str, body: str) -> None:
        credentials = self._account_and_password()
        if credentials is None:
            return
        account, password = credentials
        self.report(f"Sending to {to}…")

        def done(_result, error) -> None:
            if error is not None:
                self.report(f"Not sent: {error}", is_error=True)
                return
            self.log.success("comms", f"Sent “{subject}” to {to}")
            self.report(f"Sent “{subject}”")
            self.sent.emit()
        run_in_thread(self, lambda: send_mail(account, password, to, subject, body), done)


register_tools([
    ToolSpec("comms.refresh", "Check mail", "✉", "Fetch the newest messages of the selected account",
             lambda module: module.refresh_inbox(), "comms", "email inbox refresh"),
    ToolSpec("comms.reply", "Reply", "↩", "Reply to the open message", lambda module: module.compose_reply(), "comms", "email"),
])
# --- END [4.1] ---
