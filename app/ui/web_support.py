# ==============================================================================
# FILE: app/ui/web_support.py
# OVERVIEW: Shared web-engine plumbing for browser pages: start-up flag, availability probe, the one
#           browser profile (cookies, cache, logins kept in the DevOS data folder) and downloads that land
#           in the user's content library.
# PURPOSE:
#   1. prepare_web_engine(): must run before QApplication exists (Qt WebEngine shares OpenGL contexts).
#   2. web_engine_unavailable_reason(): None when Qt WebEngine works, else why not (logged once) - a
#      browser page shows the reason instead of crashing.
#   3. browser_profile(): persistent profile in <data folder>/browser; downloads go to the content
#      library (kind from the file extension) and are announced in the log.
#   4. address_to_url(text): what the address bar means - a URL, a host name, or a web search.
#   5. set_web_views_suspended(root, is_suspended): a page that has no tab stops its web content (sound,
#      video, scripts) by discarding its web views; they reload when the page is shown again.
# CONTRACT:
#   - Requires: PySide6 (QtWebEngineCore / QtWebEngineWidgets, optional), app.data.store.content
#               (get_content_store, kind_for_path), app.data.store.database.data_directory, app.data.activity_log
#   - Exposes: prepare_web_engine(), shutdown_web_engine(), web_engine_unavailable_reason(), browser_profile(), container_profile(name),
#              container_names(), container_slug(), address_to_url(), set_web_views_suspended(root, is_suspended),
#              HOME_URL, SEARCH_URL
# NOTES:
#   - [2026-11-17] set_web_views_suspended(): closed pages kept playing music (a YouTube Music web app went on
#                  after every tab was closed). Discarded web views stop and free their memory.
#   - [2026-11-16] shutdown_web_engine(): pages are deleted before their profiles on exit (the app and the test
#                  run used to end in a Qt WebEngine segfault).
#   - [2026-11-16] Containers: container_profile(name) gives a site its own persistent profile (separate logins)
#                  for the Web App module; profile setup shared in _make_profile().
#   - [2026-11-16] Created for browser pages. Downloads never ask where to save: they become library items.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - HOME_URL (str): Where a new browser page starts and Home goes (default: DuckDuckGo).
# - SEARCH_URL (str): Search used when the address bar text isn't an address ({} = the query).
# - profile: Qt WebEngine's store for cookies, cache, local storage and permissions.
# - discarded (web view): Qt WebEngine's Discarded lifecycle state - the page's process is gone (no sound, no
#   scripts, no memory); showing the view again reloads its address.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. WEB_SUPPORT
#    1.1 imports_and_constants
#    1.2 availability
#    1.3 profile_and_downloads
#    1.4 address_to_url
#    1.5 suspend_web_views
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_constants
# ==============================================================================
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote_plus

from PySide6.QtCore import QCoreApplication, Qt

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store, kind_for_path
from app.data.store.database import data_directory

HOME_URL = "https://duckduckgo.com/"
SEARCH_URL = "https://duckduckgo.com/?q={}"
_UNKNOWN = object()
_unavailable_reason = _UNKNOWN
_profile = None
_containers: dict = {}                 # slug -> QWebEngineProfile (named containers)
_active_downloads: list = []          # keep download requests alive until they finish
# --- END [1.1] ---

# ==============================================================================
# [1.2] availability
# ==============================================================================
def prepare_web_engine() -> None:
    """Call before creating QApplication: Qt WebEngine needs shared OpenGL contexts."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def shutdown_web_engine() -> None:
    """Delete every web page before its profile, then the profiles. Without this, Python's exit tears Qt
    down in an arbitrary order and Qt WebEngine crashes (segfault) when a profile dies before its pages."""
    global _profile
    if _profile is None and not _containers:
        return
    from PySide6.QtCore import QEvent
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
    for widget in QApplication.allWidgets():
        if isinstance(widget, QWebEngineView):
            page = widget.page()
            if page is not None:
                page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for profile in [_profile, *_containers.values()]:
        if profile is not None:
            profile.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _profile = None
    _containers.clear()


def web_engine_unavailable_reason() -> str | None:
    global _unavailable_reason
    if _unavailable_reason is _UNKNOWN:
        try:
            from PySide6 import QtWebEngineCore, QtWebEngineWidgets  # noqa: F401
            _unavailable_reason = None
            get_activity_log().success("browser", "Web engine (Qt WebEngine) available")
        except ImportError as error:
            _unavailable_reason = f"Qt WebEngine is not installed ({error})"
            get_activity_log().warning("browser", f"Browser pages unavailable: {_unavailable_reason}")
    return _unavailable_reason
# --- END [1.2] ---

# ==============================================================================
# [1.3] profile_and_downloads
# ==============================================================================
def _make_profile(storage_name: str, folder: Path):
    from PySide6.QtWebEngineCore import QWebEngineProfile
    folder.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile(storage_name, QCoreApplication.instance())
    profile.setPersistentStoragePath(str(folder / "storage"))
    profile.setCachePath(str(folder / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
    profile.downloadRequested.connect(_on_download_requested)
    return profile


def browser_profile():
    """The shared, persistent browser profile (created on first use)."""
    global _profile
    if _profile is None:
        folder = data_directory() / "browser"
        _profile = _make_profile("devos", folder)
        get_activity_log().info("browser", f"Browser profile: {folder}")
    return _profile


def container_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "container"


def container_profile(name: str):
    """A named, persistent profile of its own (cookies, logins, storage): sites in one container can't
    see another container's logins. Created on first use; kept in <data>/browser-containers/<slug>."""
    slug = container_slug(name)
    if slug not in _containers:
        folder = data_directory() / "browser-containers" / slug
        _containers[slug] = _make_profile(f"devos-{slug}", folder)
        get_activity_log().info("browser", f"Container “{name}”: {folder}")
    return _containers[slug]


def container_names() -> list[str]:
    folder = data_directory() / "browser-containers"
    return sorted(path.name for path in folder.iterdir() if path.is_dir()) if folder.exists() else []


def _on_download_requested(request) -> None:
    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
    original_name = request.downloadFileName() or "download"
    kind = kind_for_path(original_name)
    store = get_content_store()
    path = store.new_path(kind, Path(original_name).suffix.lower())
    request.setDownloadDirectory(str(path.parent))
    request.setDownloadFileName(path.name)
    log = get_activity_log()
    log.info("browser", f"Downloading {original_name} into your Library…")
    _active_downloads.append(request)

    def on_state_changed(state) -> None:
        states = QWebEngineDownloadRequest.DownloadState
        if state == states.DownloadCompleted:
            store.register_path(kind, store.unique_name(kind, Path(original_name).stem), path,
                                {"source_url": request.url().toString()})
        elif state in (states.DownloadCancelled, states.DownloadInterrupted):
            log.warning("browser", f"Download of {original_name} stopped: {request.interruptReasonString() or 'cancelled'}")
            path.unlink(missing_ok=True)
        else:
            return
        if request in _active_downloads:
            _active_downloads.remove(request)

    request.stateChanged.connect(on_state_changed)
    request.accept()
# --- END [1.3] ---

# ==============================================================================
# [1.4] address_to_url
# ==============================================================================
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_HOST = re.compile(r"^(localhost|\[[0-9a-fA-F:]+\]|[\w-]+(\.[\w-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d+)?(/.*)?$")


def address_to_url(text: str) -> str:
    """Address bar text -> URL: full URLs as typed, host names get https://, anything else is searched."""
    text = text.strip()
    if not text:
        return HOME_URL
    if _SCHEME.match(text) or text.startswith(("about:", "file:", "data:")):
        return text
    if " " not in text and _HOST.match(text):
        is_local = text.startswith(("localhost", "127.", "[")) or re.match(r"^\d{1,3}(\.\d{1,3}){3}", text)
        return ("http://" if is_local else "https://") + text
    return SEARCH_URL.format(quote_plus(text))
# --- END [1.4] ---


# ==============================================================================
# [1.5] suspend_web_views
# ==============================================================================
def set_web_views_suspended(root, is_suspended: bool) -> int:
    """Discard (True) or wake (False) every web view inside `root`; returns how many changed. Only hidden
    views can be discarded (Qt's rule) - visible ones are left alone."""
    if _profile is None and not _containers:
        return 0                                     # no web view was ever made
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    states = QWebEnginePage.LifecycleState
    changed = 0
    for view in root.findChildren(QWebEngineView):
        page = view.page()
        if page is None:
            continue
        if is_suspended and not page.isVisible() and page.lifecycleState() != states.Discarded:
            page.setLifecycleState(states.Discarded)
            changed += 1
        elif not is_suspended and page.lifecycleState() != states.Active:
            page.setLifecycleState(states.Active)
            changed += 1
    return changed
# --- END [1.5] ---
