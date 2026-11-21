# ==============================================================================
# FILE: nix/native.nix
# OVERVIEW: DevOS's native C++ maths (core/src -> the devos_core Python extension) as a Nix package, built
#           once by Nix instead of on the first launch (run.sh's `python -m app.native.build --if-missing`).
# PURPOSE:
#   1. Builds core/CMakeLists.txt against the same Python the app runs with (the extension's ABI must match).
#   2. Installs devos_core*.so into lib/devos-native/; nix/package.nix copies it to where app/native looks
#      (core/build/ beside the app's sources).
# CONTRACT:
#   - Requires: callPackage arguments (stdenv, cmake, python3, lib).
#   - Exposes: the derivation (pname devos-native; "devos-core" is reserved for the headless engine service).
# NOTES:
#   - [2026-11-20] Created in Phase 6a.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None — relies solely on global README Lexicon.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PACKAGE
#    1.1 derivation
# ==============================================================================

{ lib, stdenv, cmake, python3 }:

# ==============================================================================
# [1.1] derivation
# ==============================================================================
let
  python = python3.withPackages (ps: [ ps.pybind11 ]);
in
stdenv.mkDerivation {
  pname = "devos-native";
  version = "0.3.0";
  src = lib.fileset.toSource {
    root = ../core;
    fileset = lib.fileset.unions [ ../core/CMakeLists.txt ../core/src ];
  };

  nativeBuildInputs = [ cmake python ];

  preConfigure = ''
    cmakeFlagsArray+=("-Dpybind11_DIR=$(python -m pybind11 --cmakedir)" "-DPython_EXECUTABLE=${python.interpreter}")
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 -t $out/lib/devos-native devos_core*.so
    runHook postInstall
  '';

  meta = {
    description = "DevOS native maths (devos_core Python extension; NumPy fallback in app/native)";
    platforms = lib.platforms.linux;
  };
}
# --- END [1.1] ---
