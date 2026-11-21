# ==============================================================================
# FILE: nix/physics.nix
# OVERVIEW: The DevOS physics engine (core/physics) as a Nix package: libdevos_physics.so, its public headers,
#           devos_physics_cli and the devos_physics Python module. Its CTest suite runs as part of the build.
# PURPOSE:
#   1. A reproducible build of core/physics: MuJoCo and Eigen from the pinned nixpkgs, Jolt from the same
#      release archive and SHA256 that core/physics/CMakeLists.txt pins (Nix builds have no network, so CMake
#      is pointed at the unpacked sources with FETCHCONTENT_SOURCE_DIR_JOLTPHYSICS).
#   2. doCheck: the physics tests and the --verify runs (determinism, seek, branch) must pass, or the package
#      doesn't build.
# CONTRACT:
#   - Requires: callPackage arguments (stdenv, cmake, eigen, mujoco, python3, fetchurl, runCommand, lib).
#   - Exposes: the derivation; `passthru.pythonPath` (where the Python module lands, for PYTHONPATH).
# NOTES:
#   - [2026-11-20] Created in Phase 6a. To bump Jolt, change version and hash here AND in the CMake file.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - joltVersion / joltHash: must match DEVOS_PHYSICS_JOLT_VERSION and URL_HASH in core/physics/CMakeLists.txt.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PACKAGE
#    1.1 sources
#    1.2 derivation
# ==============================================================================

{ lib, stdenv, cmake, eigen, mujoco, python3, fetchurl, runCommand }:

# ==============================================================================
# [1.1] sources
# ==============================================================================
let
  joltVersion = "5.7.0";
  joltArchive = fetchurl {
    url = "https://github.com/jrouwe/JoltPhysics/archive/refs/tags/v${joltVersion}.tar.gz";
    sha256 = "9a0a405f2375dc4bd9f72486c56e1cbc7794c6a62e42fc48fadea45dfc93dacc";
  };
  jolt = runCommand "jolt-physics-${joltVersion}-source" { } ''
    tar xzf ${joltArchive}
    mv JoltPhysics-${joltVersion} $out
  '';
  python = python3.withPackages (ps: [ ps.pybind11 ]);
  pythonDir = "lib/${python3.libPrefix}/site-packages";
# --- END [1.1] ---

# ==============================================================================
# [1.2] derivation
# ==============================================================================
in
stdenv.mkDerivation {
  pname = "devos-physics";
  version = "0.1.0";
  src = lib.fileset.toSource {
    root = ../core/physics;
    fileset = lib.fileset.difference ../core/physics
      (lib.fileset.maybeMissing ../core/physics/build);
  };

  nativeBuildInputs = [ cmake python ];
  buildInputs = [ eigen mujoco ];

  cmakeFlags = [
    "-DCMAKE_BUILD_TYPE=Release"
    "-DFETCHCONTENT_SOURCE_DIR_JOLTPHYSICS=${jolt}"
    "-DFETCHCONTENT_FULLY_DISCONNECTED=ON"
    "-DPython_EXECUTABLE=${python.interpreter}"
    "-DDEVOS_PHYSICS_PYTHON_INSTALL_DIR=${pythonDir}"
  ];

  doCheck = true;                     # ctest: unit tests + determinism / seek / branch --verify runs

  passthru.pythonPath = pythonDir;

  meta = {
    description = "DevOS physics engine: MuJoCo + Jolt behind one deterministic API, a CLI and a Python module";
    platforms = lib.platforms.linux;
    mainProgram = "devos_physics_cli";
  };
}
# --- END [1.2] ---
