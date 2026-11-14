# Changelog

All notable changes to DevOS. Versions follow [Semantic Versioning](https://semver.org).
Newest first. Each version has a matching git tag (`v0.1.0` = version 0.1.0).

## [Unreleased]

## [0.1.0] - 2026-11-14
### Added
- PySide6 main window with collapsible, resizable sidebar and page stack.
- Home dashboard with live CPU / memory / disk / uptime cards and activity feed.
- Cost Tracking page loaded from a Qt Designer form (`cost_dashboard.ui`).
- Flat dark "pro tool" theme in `theme.py`.
- Desktop launcher (`run.sh`, `install-desktop.sh`).
- Code standard (header, definitions, index, sections) and naming standard across `app/`.
- README Lexicon & naming dictionary.

### Fixed
- `//*` typo in `theme.py` that silently dropped the sidebar styles.
