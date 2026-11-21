# ==============================================================================
# FILE: nix/python.nix
# OVERVIEW: The one list of Python libraries DevOS runs with. The package (nix/package.nix), the test check and
#           the development shell (shell.nix) all build their interpreter from it, so they never drift apart.
# PURPOSE:
#   1. runtime(ps): what `devos` needs at run time.
#   2. development(ps): runtime + build and test tools (pytest, pybind11).
# CONTRACT:
#   - Requires: nothing (plain functions over a nixpkgs python package set).
#   - Exposes: { runtime, development } - each `ps: [ ... ]`, for python3.withPackages.
# NOTES:
#   - [2026-11-20] Created in Phase 6a from shell.nix's list (which now imports it).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None — relies solely on global README Lexicon.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LISTS
#    1.1 runtime_and_development
# ==============================================================================

# ==============================================================================
# [1.1] runtime_and_development
# ==============================================================================
rec {
  runtime = ps: with ps; [
    pyside6
    psutil         # system and device stats
    numpy          # n-dimensional arrays (Tensor4D)
    pyqtgraph      # fast plots: waterfall + 3D scatter (runs on PySide6)
    pyopengl       # OpenGL for pyqtgraph's 3D view
    trimesh        # CAD mesh import (.stl / .obj) for the wave simulator and the CAD module
    shapely        # 2D sketch geometry for CAD part design (extrude needs it)
    mapbox-earcut  # polygon triangulation used by trimesh's extrude
    manifold3d     # robust mesh booleans for CAD part design (pocket / hole / union)
    networkx       # graph layouts for the graph database view
    kuzu           # embedded graph database (Graph database template)
    openpyxl       # .xlsx read / write for the Spreadsheet template
    keyring        # system keyring for Comms passwords and Agents API keys (never stored in SQLite)
    pyarrow        # Apache Arrow: binary encoding of big data shapes between engines and views
  ];

  development = ps: runtime ps ++ (with ps; [
    pybind11       # C++ <-> Python bindings (python -m app.native.build)
    pytest         # tests/ (python -m pytest -q)
  ]);
}
# --- END [1.1] ---
