# ==============================================================================
# FILE: core/physics/CMakeLists.txt
# OVERVIEW: The DevOS physics engine as an independent CMake project: libdevos_physics.so (C++20, MuJoCo +
#           Jolt behind one API), the devos_physics_cli headless runner, a test executable (CTest) and an
#           optional thin pybind11 adapter (the devos_physics Python module).
# USAGE:
#   cmake -S core/physics -B core/physics/build -DCMAKE_BUILD_TYPE=Release
#   cmake --build core/physics/build -j
#   ctest --test-dir core/physics/build --output-on-failure
#   core/physics/build/devos_physics_cli core/physics/scenes/demo --seconds 2 --verify
# NOTES:
#   - [2026-11-16] Created. Strict boundary: nothing here references app/ (or any path outside this folder).
#                  Eigen3 and MuJoCo come from the system (nix-shell); Jolt is fetched at a pinned tag because
#                  nixpkgs doesn't package it; pybind11 is only needed for the Python adapter.
#   - [2026-11-16] EIGEN_MAX_ALIGN_BYTES=16 on the public interface (Jolt's -mavx2 made the library and its
#                  callers disagree on Eigen's alignment: an intermittent crash in PhysicsWorld::pose()).
#   - [2026-11-17] Jolt is fetched as the v5.7.0 release archive with a pinned SHA256 (URL_HASH) instead of a
#                  git tag, so every build verifies the exact sources. Offline / Nix builds point
#                  FETCHCONTENT_SOURCE_DIR_JOLTPHYSICS at an unpacked copy. A third CTest entry runs --verify
#                  with a PID loop (controller snapshots in the worldline).
#   - [2026-11-20] install() rules (library, public headers, CLI, Python module under
#                  DEVOS_PHYSICS_PYTHON_INSTALL_DIR) so Nix can package it (nix/physics.nix); RPATHs point at
#                  the installed library. Phase 6a.
# ==============================================================================
cmake_minimum_required(VERSION 3.24)
project(devos_physics VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)        # Jolt is linked statically into the shared library
include(GNUInstallDirs)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(DEVOS_PHYSICS_PYTHON "Build the pybind11 adapter (devos_physics Python module)" ON)
option(DEVOS_PHYSICS_TESTS "Build the test executable and register it with CTest" ON)

# ---- third-party libraries ----------------------------------------------------------------------------
find_package(Eigen3 3.4 REQUIRED NO_MODULE)
find_package(mujoco REQUIRED)

include(FetchContent)
set(DOUBLE_PRECISION OFF CACHE BOOL "" FORCE)
set(CROSS_PLATFORM_DETERMINISTIC ON CACHE BOOL "" FORCE)    # same results on every machine
set(CPP_RTTI_ENABLED ON CACHE BOOL "" FORCE)                # pybind11 / exceptions need RTTI
set(CPP_EXCEPTIONS_ENABLED ON CACHE BOOL "" FORCE)
set(INTERPROCEDURAL_OPTIMIZATION OFF CACHE BOOL "" FORCE)
set(ENABLE_ALL_WARNINGS OFF CACHE BOOL "" FORCE)
set(DEBUG_RENDERER_IN_DEBUG_AND_RELEASE OFF CACHE BOOL "" FORCE)
set(PROFILER_IN_DEBUG_AND_RELEASE OFF CACHE BOOL "" FORCE)
set(OVERRIDE_CXX_FLAGS OFF CACHE BOOL "" FORCE)             # don't let Jolt replace our compiler flags
# The release archive is checked against its SHA256: a changed or tampered download fails the configure.
# To bump Jolt, change the version and the hash together (sha256sum of the new archive). Offline builds:
# -DFETCHCONTENT_SOURCE_DIR_JOLTPHYSICS=/path/to/JoltPhysics-<version> skips the download.
set(DEVOS_PHYSICS_JOLT_VERSION 5.7.0)
FetchContent_Declare(JoltPhysics
  URL https://github.com/jrouwe/JoltPhysics/archive/refs/tags/v${DEVOS_PHYSICS_JOLT_VERSION}.tar.gz
  URL_HASH SHA256=9a0a405f2375dc4bd9f72486c56e1cbc7794c6a62e42fc48fadea45dfc93dacc
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  SOURCE_SUBDIR Build)
FetchContent_MakeAvailable(JoltPhysics)

# ---- libdevos_physics -----------------------------------------------------------------------------------
add_library(devos_physics SHARED
  src/worldline_buffer.cpp
  src/controller_port.cpp
  src/scene_loader.cpp
  src/jolt_world.cpp
  src/coupled_stepper.cpp
  src/physics_world.cpp)
target_include_directories(devos_physics PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(devos_physics PUBLIC Eigen3::Eigen PRIVATE Jolt mujoco::mujoco)
# Jolt compiles our sources with -mavx2, which raises Eigen's default alignment to 32 bytes. Callers (the CLI,
# Python, DevOS) may be built without AVX, so Eigen types crossing the API (Pose, Vec3, Quat) would disagree on
# layout / alignment. Pinning the alignment in the PUBLIC interface keeps the ABI the same everywhere.
target_compile_definitions(devos_physics PUBLIC EIGEN_MAX_ALIGN_BYTES=16)
target_compile_options(devos_physics PRIVATE -Wall -Wextra)
set_target_properties(devos_physics PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION 0)

# ---- headless runner ---------------------------------------------------------------------------------------
add_executable(devos_physics_cli tools/physics_cli.cpp)
target_link_libraries(devos_physics_cli PRIVATE devos_physics)

# ---- tests -------------------------------------------------------------------------------------------------
if(DEVOS_PHYSICS_TESTS)
  enable_testing()
  add_executable(devos_physics_tests tests/test_physics.cpp)
  target_link_libraries(devos_physics_tests PRIVATE devos_physics)
  target_compile_definitions(devos_physics_tests PRIVATE DEVOS_PHYSICS_SCENES="${CMAKE_CURRENT_SOURCE_DIR}/scenes")
  add_test(NAME devos_physics_tests COMMAND devos_physics_tests)
  add_test(NAME devos_physics_cli_verify COMMAND devos_physics_cli ${CMAKE_CURRENT_SOURCE_DIR}/scenes/demo
           --seconds 1 --set shoulder_motor=0.6 --verify)
  add_test(NAME devos_physics_cli_verify_pid COMMAND devos_physics_cli ${CMAKE_CURRENT_SOURCE_DIR}/scenes/demo
           --seconds 1 --set shoulder_motor=0.6 --pid shoulder_motor=2,8,0.05 --verify)
endif()

# ---- thin Python adapter -----------------------------------------------------------------------------------
if(DEVOS_PHYSICS_PYTHON)
  find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
  if(NOT pybind11_DIR)
    execute_process(COMMAND ${Python_EXECUTABLE} -m pybind11 --cmakedir OUTPUT_VARIABLE pybind11_DIR OUTPUT_STRIP_TRAILING_WHITESPACE)
  endif()
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(devos_physics_python python/bindings.cpp)
  set_target_properties(devos_physics_python PROPERTIES OUTPUT_NAME devos_physics
                        BUILD_RPATH "$<TARGET_FILE_DIR:devos_physics>")
  target_link_libraries(devos_physics_python PRIVATE devos_physics)
endif()

# ---- install (nix/physics.nix; `cmake --install`) --------------------------------------------------------
# Jolt is static and private, so only our own library, headers, the CLI and the Python module are installed.
set(DEVOS_PHYSICS_PYTHON_INSTALL_DIR "${CMAKE_INSTALL_LIBDIR}/python" CACHE STRING
    "Where the devos_physics Python module is installed (relative to the prefix)")
set_target_properties(devos_physics_cli PROPERTIES INSTALL_RPATH "${CMAKE_INSTALL_FULL_LIBDIR}")
install(TARGETS devos_physics LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(TARGETS devos_physics_cli RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
if(DEVOS_PHYSICS_PYTHON)
  set_target_properties(devos_physics_python PROPERTIES INSTALL_RPATH "${CMAKE_INSTALL_FULL_LIBDIR}")
  install(TARGETS devos_physics_python LIBRARY DESTINATION ${DEVOS_PHYSICS_PYTHON_INSTALL_DIR})
endif()
