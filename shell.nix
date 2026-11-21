# ==============================================================================
# FILE: shell.nix
# OVERVIEW: The DevOS development shell: Python with every library (nix/python.nix), the C++ toolchain for
#           core/ and core/physics/, Qt's plugins, and optionally n8n. Also the flake's devShells.default.
# PURPOSE:
#   1. nix-shell (or `nix develop`): run, test and build DevOS from the checkout (python -m app.main).
#   2. Same nixpkgs as the flake (flake.lock, via nix/pinned.nix), so the shell matches the package.
# CONTRACT:
#   - Requires: flake.lock (when pkgs isn't passed in).
#   - Exposes: the mkShell derivation; arguments pkgs, withN8n (default true; --arg withN8n false skips it).
# NOTES:
#   - [2026-11-20] Phase 6a: pinned nixpkgs (was <nixpkgs>), Python list moved to nix/python.nix, the n8n rule to
#                  nix/pkgs.nix; standard header.
# ==============================================================================

{ pkgs ? (import ./nix/pinned.nix).pkgs
, withN8n ? true   # nix-shell --arg withN8n false skips n8n (large) when you don't use the n8n web app
}:

let
  python = pkgs.python3.withPackages (import ./nix/python.nix).development;
in
pkgs.mkShell {
  packages = [
    python
    pkgs.cmake
    pkgs.gcc
    pkgs.eigen       # core/physics: linear algebra (Eigen3)
    pkgs.mujoco      # core/physics: articulated-body solver (MuJoCo C API); Jolt is fetched by CMake (pinned SHA256)
    pkgs.gdb
    pkgs.qt6.qtbase
    pkgs.qt6.qtwayland   # native Wayland support (KDE/Plasma)
    pkgs.qt6.qtsvg
    pkgs.git
    pkgs.gh
  ] ++ pkgs.lib.optionals withN8n [
    pkgs.n8n         # workflows: the Web App's "n8n" preset starts this one (same version for everyone, no npm -g)
  ];

  shellHook = ''
    # Let Qt find its platform plugins inside the Nix store
    export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtwayland}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtsvg}/${pkgs.qt6.qtbase.qtPluginPrefix}"
    export PYTHONPATH="$PWD:$PYTHONPATH"
    echo "DevOS shell ready — run:  python -m app.main"
  '';
}
