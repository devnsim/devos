# ==============================================================================
# FILE: app/ui/plot_tools.py
# OVERVIEW: pyqtgraph helpers so every plot in every module looks and behaves the same.
# PURPOSE:
#   1. styled_plot(): a PlotWidget with the DevOS background, muted axes, no context menu / "A" button
#      and no SI-prefix rescaling (metres stay metres).
#   2. style_plot_item(): the same styling for a PlotItem made elsewhere (e.g. in a GraphicsLayout).
# CONTRACT:
#   - Requires: pyqtgraph, app.ui.theme (BACKGROUND_COLOR, BORDER_COLOR, MUTED_TEXT_COLOR)
#   - Exposes: styled_plot(x_label="", y_label="", axes=("left", "bottom")), style_plot_item(plot_item, axes)
# NOTES:
#   - [2026-11-15] Created in the health pass: the same 10 lines lived in rf_imaging, wave_sim,
#                  tensor_inspector and trading_chart.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - SI prefix: pyqtgraph's habit of rescaling an axis (e.g. "x0.001"); always off here.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PLOTS
#    1.1 style_plot_item
#    1.2 styled_plot
# ==============================================================================

import pyqtgraph as pg

from app.ui.theme import BACKGROUND_COLOR, BORDER_COLOR, MUTED_TEXT_COLOR


# ==============================================================================
# [1.1] style_plot_item
# ==============================================================================
def style_plot_item(plot_item: pg.PlotItem, axes: tuple[str, ...] = ("left", "bottom")) -> pg.PlotItem:
    plot_item.setMenuEnabled(False)
    plot_item.hideButtons()
    for axis_name in axes:
        axis = plot_item.getAxis(axis_name)
        axis.enableAutoSIPrefix(False)
        axis.setPen(pg.mkPen(BORDER_COLOR))
        axis.setTextPen(pg.mkPen(MUTED_TEXT_COLOR))
    return plot_item
# --- END [1.1] ---

# ==============================================================================
# [1.2] styled_plot
# ==============================================================================
def styled_plot(x_label: str = "", y_label: str = "", axes: tuple[str, ...] = ("left", "bottom")) -> pg.PlotWidget:
    plot = pg.PlotWidget(background=BACKGROUND_COLOR)
    style_plot_item(plot.getPlotItem(), axes)
    if x_label:
        plot.getPlotItem().setLabel("bottom", x_label)
    if y_label:
        plot.getPlotItem().setLabel("left", y_label)
    return plot
# --- END [1.2] ---
