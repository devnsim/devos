# ==============================================================================
# FILE: app/native/build.py
# OVERVIEW: Builds the C++ engine (core/ -> core/build/devos_core*.so) with CMake.
# PURPOSE:
#   1. Configures and builds core/ with the nix-shell's cmake, compiler and pybind11.
#   2. --if-missing: skips the build unless the library is missing or older than its sources.
# USAGE:
#   python -m app.native.build               configure + build
#   python -m app.native.build --if-missing  only if devos_core*.so is missing or older than the C++
#                                            sources (used by run.sh)
# CONTRACT:
#   - Requires: cmake, a C++20 compiler, pybind11 (all in shell.nix)
# NOTES:
#   - [2026-11-15] Created. Failure is reported but not fatal: DevOS falls back to the NumPy engine.
#   - [2026-11-15] Brought to the full file standard (PURPOSE, DEFINITIONS, INDEX, sections).
#   - [2026-11-15] --if-missing also rebuilds a stale library (sources newer than the .so), so new
#                  native functions reach existing installs.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - stale: The built library is older than CMakeLists.txt or any core/src/*.cpp / *.hpp.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. BUILD
#    1.1 imports_and_paths
#    1.2 build
#    1.3 command_line
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_paths
# ==============================================================================
import argparse
import subprocess
import sys
from pathlib import Path

CORE_DIRECTORY = Path(__file__).resolve().parents[2] / "core"
BUILD_DIRECTORY = CORE_DIRECTORY / "build"
# --- END [1.1] ---


# ==============================================================================
# [1.2] build
# ==============================================================================


def _is_stale() -> bool:
    libraries = list(BUILD_DIRECTORY.glob("devos_core*.so"))
    if not libraries:
        return True
    sources = [CORE_DIRECTORY / "CMakeLists.txt", *(CORE_DIRECTORY / "src").glob("*.[ch]pp")]
    return max(path.stat().st_mtime for path in sources) > min(path.stat().st_mtime for path in libraries)


def build(if_missing: bool = False) -> bool:
    if if_missing and not _is_stale():
        return True
    import pybind11
    configure = [
        "cmake", "-S", str(CORE_DIRECTORY), "-B", str(BUILD_DIRECTORY), "-DCMAKE_BUILD_TYPE=Release",
        f"-Dpybind11_DIR={pybind11.get_cmake_dir()}", f"-DPython_EXECUTABLE={sys.executable}",
    ]
    compile_ = ["cmake", "--build", str(BUILD_DIRECTORY), "-j"]
    for command in (configure, compile_):
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            print(f"[DevOS] native build failed ({' '.join(command[:2])}):\n{completed.stdout[-3000:]}\n{completed.stderr[-3000:]}")
            return False
    print(f"[DevOS] built {next(BUILD_DIRECTORY.glob('devos_core*.so')).name}")
    return True
# --- END [1.2] ---


# ==============================================================================
# [1.3] command_line
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build DevOS's C++ engine")
    parser.add_argument("--if-missing", action="store_true")
    sys.exit(0 if build(parser.parse_args().if_missing) else 1)
# --- END [1.3] ---
