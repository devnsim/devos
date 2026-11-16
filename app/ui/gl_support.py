# ==============================================================================
# FILE: app/ui/gl_support.py
# OVERVIEW: One place that decides whether pyqtgraph's OpenGL 3D views can be used on this machine.
# PURPOSE:
#   1. Imports pyqtgraph.opengl if PyOpenGL is installed (exposed as gl).
#   2. Probes once for a real OpenGL >= 2.1 context and caches the answer.
# CONTRACT:
#   - Requires: pyqtgraph, PyOpenGL (optional), a QGuiApplication before opengl_unavailable_reason()
#   - Exposes: gl (module or None), HAS_OPENGL, MIN_OPENGL_VERSION, opengl_unavailable_reason()
# NOTES:
#   - [2026-11-15] The probe result is announced in the activity log (OpenGL version or why 3D is off).
#   - [2026-11-15] Moved out of tensor_inspector_module.py so every 3D module shares one probe.
#                  GLViewWidget fails only inside initializeGL() on first show (uncatchable there and
#                  it took the app down), so modules must check this before creating one.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - MIN_OPENGL_VERSION (tuple): Oldest OpenGL pyqtgraph.opengl supports: (2, 1).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. OPENGL
#    1.1 import_and_probe
# ==============================================================================

# ==============================================================================
# [1.1] import_and_probe
# ==============================================================================
from app.data.activity_log import get_activity_log

try:
    import pyqtgraph.opengl as gl
    HAS_OPENGL = True
except Exception:                                # PyOpenGL missing or no GL library
    gl = None
    HAS_OPENGL = False

MIN_OPENGL_VERSION = (2, 1)                      # pyqtgraph.opengl's own minimum
_opengl_probe_result: str | None = None          # cached: "" = usable, otherwise the reason it isn't


def opengl_unavailable_reason() -> str:
    """"" if a real OpenGL >= 2.1 context can be made; otherwise why not. Probed once, then cached.
    GLViewWidget only fails later, inside initializeGL() on first show, which can't be caught there."""
    global _opengl_probe_result
    if _opengl_probe_result is not None:
        return _opengl_probe_result
    if not HAS_OPENGL:
        _opengl_probe_result = "PyOpenGL not installed"
        get_activity_log().warning("graphics", "3D views disabled: PyOpenGL not installed")
        return _opengl_probe_result
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        _opengl_probe_result = f"no OpenGL on the '{QGuiApplication.platformName()}' platform"
        get_activity_log().warning("graphics", f"3D views disabled: {_opengl_probe_result}")
        return _opengl_probe_result
    context = QOpenGLContext()
    surface = QOffscreenSurface()
    surface.create()
    if not context.create() or not context.makeCurrent(surface):
        _opengl_probe_result = "could not create an OpenGL context"
    else:
        version = context.format().version()
        context.doneCurrent()
        is_usable = (version[0], version[1]) >= MIN_OPENGL_VERSION
        _opengl_probe_result = "" if is_usable else f"OpenGL {version[0]}.{version[1]} < 2.1"
        if is_usable:
            get_activity_log().success("graphics", f"OpenGL {version[0]}.{version[1]} available for 3D views")
    surface.destroy()
    if _opengl_probe_result:
        get_activity_log().warning("graphics", f"3D views disabled: {_opengl_probe_result}")
    return _opengl_probe_result
# --- END [1.1] ---
