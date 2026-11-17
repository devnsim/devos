# ==============================================================================
# FILE: app/data/store/secrets.py
# OVERVIEW: Secrets (passwords, API keys) in the system keyring - never in the DevOS database. When no
#           keyring is usable, a secret lives in memory for this session only.
# PURPOSE:
#   1. One place for every module that holds a secret (Comms passwords, Agents API keys).
#   2. Say honestly where secrets end up (secret_storage()), so the UI can tell the user.
# CONTRACT:
#   - Requires: keyring (optional; no package or no backend = session memory). No Qt.
#   - Exposes: get_secret(service, name), set_secret(service, name, value, remember=True),
#              forget_secret(service, name), secret_storage() -> str
# NOTES:
#   - [2026-11-17] Created from Comms' password helpers, now shared with the Agents module's API keys.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - service (str): Keyring service a module's secrets are stored under ("devos-comms", "devos-agents").
# - name (str): The secret's name within its service (an account id, a provider).
# - session secret: A secret kept in memory only (no keyring, or the user chose not to remember it).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SECRETS
#    1.1 keyring_access
#    1.2 secret_functions
# ==============================================================================

# ==============================================================================
# [1.1] keyring_access
# ==============================================================================
from __future__ import annotations

_session_secrets: dict[tuple[str, str], str] = {}


def _keyring():
    try:
        import keyring
        keyring.get_keyring()
        return keyring
    except Exception:                                  # no package, or no usable backend (no Secret Service)
        return None


def secret_storage() -> str:
    ring = _keyring()
    if ring is None:
        return "this session only (no system keyring)"
    name = type(ring.get_keyring()).__name__
    return "nowhere (no keyring backend)" if "fail" in name.lower() else f"system keyring ({name})"
# --- END [1.1] ---


# ==============================================================================
# [1.2] secret_functions
# ==============================================================================
def set_secret(service: str, name: str, value: str, remember: bool = True) -> None:
    _session_secrets[(service, name)] = value
    ring = _keyring()
    if remember and ring is not None:
        try:
            ring.set_password(service, name, value)
        except Exception:                              # keyring locked / unavailable: session memory still works
            pass


def get_secret(service: str, name: str) -> str | None:
    if (service, name) in _session_secrets:
        return _session_secrets[(service, name)]
    ring = _keyring()
    if ring is not None:
        try:
            return ring.get_password(service, name)
        except Exception:
            return None
    return None


def forget_secret(service: str, name: str) -> None:
    _session_secrets.pop((service, name), None)
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(service, name)
        except Exception:
            pass
# --- END [1.2] ---
