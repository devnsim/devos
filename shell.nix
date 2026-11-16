#--DevOS development shell--

# Purpose:
# This shell brings in packages that will be called upon later and spins up a nix shell environment to run them in.


#------------------------------------

{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python3.withPackages (ps: with ps; [
    pyside6
    psutil      # System and device stats
    pybind11    # C++ <-> Python bindings
    numpy       # n-dimensional arrays (Tensor4D)
    pyqtgraph   # fast plots: waterfall + 3D scatter (runs on PySide6)
    pyopengl    # OpenGL for pyqtgraph's 3D view
    trimesh     # CAD mesh import (.stl / .obj) for the wave simulator and the CAD module
    shapely     # 2D sketch geometry for CAD part design (extrude needs it)
    mapbox-earcut  # polygon triangulation used by trimesh's extrude
    manifold3d  # robust mesh booleans for CAD part design (pocket / hole / union)
    networkx    # graph layouts for the graph database view
    kuzu        # embedded graph database (Graph DB module)
    openpyxl    # .xlsx read / write for the Spreadsheet module (LibreOffice handles .ods / .xls)
    keyring     # system keyring for Comms account passwords (never stored in SQLite)
    pytest      # tests/ (python -m pytest -q)
  ]);
in
pkgs.mkShell {
  packages = [
    python
    pkgs.cmake
    pkgs.gcc
    pkgs.gdb
    pkgs.qt6.qtbase
    pkgs.qt6.qtwayland   # native Wayland support (KDE/Plasma)
    pkgs.qt6.qtsvg
    pkgs.git
    pkgs.gh
  ];

  shellHook = ''
    # Let Qt find its platform plugins inside the Nix store
    export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtwayland}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtsvg}/${pkgs.qt6.qtbase.qtPluginPrefix}"
    export PYTHONPATH="$PWD:$PYTHONPATH"
    echo "DevOS shell ready — run:  python -m app.main"
  '';
}
