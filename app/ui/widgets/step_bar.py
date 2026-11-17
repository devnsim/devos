# ==============================================================================
# FILE: app/ui/widgets/step_bar.py
# OVERVIEW: StepProgressBar - a horizontal progress bar of a workflow's steps (numbered circles joined by a
#           line that fills as steps are done), so a user sees where they are in a process at a glance.
# PURPOSE:
#   1. Shows each step as current, done, open (can start) or locked (an earlier step isn't finished);
#      a locked step's tooltip says why.
#   2. Clicking a step emits step_clicked(index); the owner decides whether to go there (and, for a locked
#      step, says why not).
# CONTRACT:
#   - Requires: PySide6, app.ui.theme colour tokens. Painted (no QSS selectors).
#   - Exposes: StepProgressBar(titles, parent=None) with signal step_clicked(int),
#              set_steps(current, states, problems), states, current; STEP_STATES
# NOTES:
#   - [2026-11-17] Created for the Part Design window (replaces its 1-4 sidebar list with a top progress bar).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - step state (str): "current", "done", "open" (can start) or "locked" (STEP_STATES).
# - CIRCLE_PX (int | px): Diameter of a step's numbered circle (default: 26).
# - HEIGHT_PX (int | px): Height of the bar (circle + title below it).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. STEP_BAR
#    1.1 imports
#    1.2 step_progress_bar
#    1.3 painting
#    1.4 mouse
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui.theme import (
    ACCENT_HIGHLIGHT_COLOR, BORDER_COLOR, EDGE_COLOR, MUTED_TEXT_COLOR, SELECTED_TEXT_COLOR, SUCCESS_COLOR, TEXT_COLOR,
)

STEP_STATES = ("current", "done", "open", "locked")
# --- END [1.1] ---


# ==============================================================================
# [1.2] step_progress_bar
# ==============================================================================
class StepProgressBar(QWidget):
    step_clicked = Signal(int)

    CIRCLE_PX = 26
    HEIGHT_PX = 58
    TOP_PX = 6

    def __init__(self, titles: list[str] | tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.titles = list(titles)
        self.current = 0
        self.states = ["current"] + ["open"] * (len(self.titles) - 1)
        self.problems = [""] * len(self.titles)
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setFixedHeight(self.HEIGHT_PX)
        self.setMinimumWidth(110 * len(self.titles))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_steps(self, current: int, states: list[str], problems: list[str] | None = None) -> None:
        """current: index of the step shown; states: one STEP_STATES value per step; problems: why a locked
        step can't start (shown as its tooltip)."""
        self.current, self.states = current, list(states)
        self.problems = list(problems or [""] * len(self.titles))
        self.update()

    def center(self, index: int) -> QPointF:
        slot = self.width() / max(1, len(self.titles))
        return QPointF(slot * (index + 0.5), self.TOP_PX + self.CIRCLE_PX / 2)

    def index_at(self, x: float) -> int | None:
        if not self.titles or self.width() <= 0:
            return None
        index = int(x // (self.width() / len(self.titles)))
        return index if 0 <= index < len(self.titles) else None
# --- END [1.2] ---

# ==============================================================================
# [1.3] painting
# ==============================================================================
    def paintEvent(self, _event) -> None:                               # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.CIRCLE_PX / 2
        for index in range(len(self.titles) - 1):              # connectors: filled up to the current step
            start, end = self.center(index), self.center(index + 1)
            is_reached = index < self.current or self.states[index + 1] in ("done", "current")
            painter.setPen(QPen(QColor(ACCENT_HIGHLIGHT_COLOR if is_reached else EDGE_COLOR), 3 if is_reached else 2))
            painter.drawLine(QPointF(start.x() + radius + 4, start.y()), QPointF(end.x() - radius - 4, end.y()))

        number_font = QFont(self.font())
        number_font.setBold(True)
        title_font = QFont(self.font())
        for index, title in enumerate(self.titles):
            state = self.states[index] if index < len(self.states) else "open"
            center = self.center(index)
            circle = QRectF(center.x() - radius, center.y() - radius, self.CIRCLE_PX, self.CIRCLE_PX)
            if state == "current":
                fill, edge, glyph_color = ACCENT_HIGHLIGHT_COLOR, ACCENT_HIGHLIGHT_COLOR, SELECTED_TEXT_COLOR
            elif state == "done":
                fill, edge, glyph_color = SUCCESS_COLOR, SUCCESS_COLOR, SELECTED_TEXT_COLOR
            elif state == "open":
                fill, edge, glyph_color = None, TEXT_COLOR if index == self._hover else EDGE_COLOR, TEXT_COLOR
            else:
                fill, edge, glyph_color = None, BORDER_COLOR, MUTED_TEXT_COLOR
            painter.setPen(QPen(QColor(edge), 2))
            painter.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)
            painter.setFont(number_font)
            painter.setPen(QColor(glyph_color))
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, "✓" if state == "done" else str(index + 1))

            title_font.setBold(state == "current")
            painter.setFont(title_font)
            is_dim = state == "locked"
            painter.setPen(QColor(MUTED_TEXT_COLOR if is_dim else TEXT_COLOR if state == "current" or index == self._hover
                                  else MUTED_TEXT_COLOR))
            slot = self.width() / len(self.titles)
            text_rect = QRectF(slot * index, circle.bottom() + 4, slot, self.HEIGHT_PX - circle.bottom() - 4)
            label = title + ("  🔒" if is_dim else "")
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(slot) - 8))
        painter.end()
# --- END [1.3] ---

# ==============================================================================
# [1.4] mouse
# ==============================================================================
    def mouseMoveEvent(self, event) -> None:                            # noqa: N802
        index = self.index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            is_locked = index is not None and self.states[index] == "locked"
            self.setCursor(Qt.CursorShape.ArrowCursor if index is None or is_locked else Qt.CursorShape.PointingHandCursor)
            self.setToolTip("" if index is None else (self.problems[index] if is_locked and self.problems[index]
                                                      else f"Step {index + 1}: {self.titles[index]}"))
            self.update()

    def leaveEvent(self, event) -> None:                                # noqa: N802
        super().leaveEvent(event)
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:                           # noqa: N802
        index = self.index_at(event.position().x())
        if event.button() == Qt.MouseButton.LeftButton and index is not None:
            self.step_clicked.emit(index)                      # locked too: the owner says why it can't start
# --- END [1.4] ---
