# ==============================================================================
# FILE: app/native/build.py
# OVERVIEW: Builds the C++ engine (core/ -> core/build/devos_core*.so) with CMake.
# USAGE:
#   python -m app.native.build               configure + build
#   python -m app.native.build --if-missing  only if no devos_core*.so exists yet (used by run.sh)
# CONTRACT:
#   - Requires: cmake, a C++20 compiler, pybind11 (all in shell.nix)
# NOTES:
#   - [2026-11-15] Created. Failure is reported but not fatal: DevOS falls back to the NumPy engine.
# ==============================================================================
import argparse
import subprocess
import sys
from pathlib import Path

CORE_DIRECTORY = Path(__file__).resolve().parents[2] / "core"
BUILD_DIRECTORY = CORE_DIRECTORY / "build"


def build(if_missing: bool = False) -> bool:
    if if_missing and any(BUILD_DIRECTORY.glob("devos_core*.so")):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build DevOS's C++ engine")
    parser.add_argument("--if-missing", action="store_true")
    sys.exit(0 if build(parser.parse_args().if_missing) else 1)
