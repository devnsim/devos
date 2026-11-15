# ==============================================================================
# FILE: core/CMakeLists.txt
# OVERVIEW: Builds devos_core, DevOS's native C++20 engine, as a Python extension (pybind11).
# USAGE: python -m app.native.build   (configures + builds into core/build/)
# NOTES:
#   - [2026-11-15] Created. pybind11 is found through its Python package (pybind11_DIR is passed by
#                  app/native/build.py), so the nix-shell provides everything.
# ==============================================================================
cmake_minimum_required(VERSION 3.20)
project(devos_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(devos_core src/wave_engine.cpp)
target_compile_options(devos_core PRIVATE -O3 -Wall -Wextra)
