# ==============================================================================
# FILE: app/__init__.py
# OVERVIEW: Marks app/ as a Python package and holds the DevOS version number.
# PURPOSE:
#   1. Lets Python import from app/ (e.g. from app.ui.main_window import MainWindow).
#   2. Stores the one official version number for the whole project.
# CONTRACT:
#   - Requires: nothing
#   - Exposes: __version__ (str)
# NOTES:
#   - [2026-11-14] Added __version__ as the single source of truth; the git tag must match it.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - __version__ (str): DevOS version as MAJOR.MINOR.PATCH; its git tag is "v" + this (e.g. v0.1.0).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. VERSION
#    1.1 version_number
# ==============================================================================

# ==============================================================================
# [1.1] version_number
# ==============================================================================
__version__ = "0.1.0"
# --- END [1.1] ---
