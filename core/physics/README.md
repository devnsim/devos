# DevOS physics engine (`core/physics/`)

A standalone C++20 library, `libdevos_physics.so`, that couples two existing solvers behind one API:

- **MuJoCo** (C API) runs the machine: articulated robots, gantries and drones.
- **Jolt Physics** runs the room: floors, fixtures and loose bodies.

The engine has no dependency on Python, Qt or DevOS's `app/`. Nothing in this folder points outside it. Python reaches it only through a thin pybind11 adapter.

## Build and run

Run these from the repository root, inside `nix-shell`. The shell provides Eigen3, MuJoCo, pybind11 and CMake. CMake downloads Jolt's v5.7.0 release archive (nixpkgs doesn't package it) and checks it against a pinned SHA256, so a changed download fails the configure. To build offline, pass `-DFETCHCONTENT_SOURCE_DIR_JOLTPHYSICS=/path/to/JoltPhysics-5.7.0`. To upgrade Jolt, change the version and the hash together in `CMakeLists.txt`.

```sh
cmake -S core/physics -B core/physics/build -DCMAKE_BUILD_TYPE=Release
cmake --build core/physics/build -j
ctest --test-dir core/physics/build --output-on-failure

# headless runner
core/physics/build/devos_physics_cli core/physics/scenes/demo --seconds 2 --set shoulder_motor=0.6 --verify
core/physics/build/devos_physics_cli core/physics/scenes/demo --seconds 2 --set shoulder_motor=0.6 \
    --pid shoulder_motor=2,8,0.05 --verify          # closed loop: PID state must survive seek / branch
core/physics/build/devos_physics_cli core/physics/scenes/demo --seconds 6 --gcode path.gcode \
    --axis X=shoulder_motor:0.0174533 --axis Y=elbow_motor:0.0174533 --csv worldline.csv

# Python (thin adapter)
PYTHONPATH=core/physics/build python -c "import devos_physics as dp; w = dp.PhysicsWorld(); w.load_scene('core/physics/scenes/demo'); w.step(1.0); print(w.summary())"
```

The build produces four targets:

| Target | What it is |
|---|---|
| `libdevos_physics.so` | The engine |
| `devos_physics_cli` | Headless runner and benchmark (`--verify` checks determinism, seek and branch) |
| `devos_physics_tests` | Self-contained tests, registered with CTest |
| `devos_physics.cpython-*.so` | The Python module (`-DDEVOS_PHYSICS_PYTHON=OFF` skips it) |

## The five components

| Component | Header | What it does |
|---|---|---|
| `PhysicsWorld` | `physics_world.hpp` | The facade. It owns the master clock and both solvers, and enforces one frame: right-handed, Z up, SI units, gravity −Z. It provides `load_scene`, `step(dt)`, `step_ticks`, `reset`, `seek(tick)` and `branch(tick)`. |
| `SceneLoader` | `scene_loader.hpp` | Reads a scene bundle. `machine.xml` (MJCF) goes to MuJoCo and `environment.xml` (MJCF) goes to Jolt. In the environment, bodies without joints are static and bodies with `<freejoint/>` are dynamic. Mesh geoms are convex hulls, such as CoACD parts. Every machine body with collision geoms gets a kinematic Jolt proxy. |
| `CoupledStepper` | `coupled_stepper.hpp` | Advances both solvers by one sub-step per tick (default 1 kHz): coupling wrench → controller → `mj_step` → mirror machine poses into Jolt → Jolt step → contacts become the next tick's `xfrc_applied`. Static fixtures push back through a penalty spring-damper. Loose bodies push back with the reaction to their momentum change. |
| `WorldlineBuffer` | `worldline_buffer.hpp` | One flat `double` row per tick in a contiguous ring. The observation block holds time, poses, joint torques, coupling forces and energies; the restore block holds the exact solver state. It is exposed zero-copy to Python and NumPy. |
| `ControllerPort` | `controller_port.hpp` | Writes `mjData->ctrl` from raw values, setpoints, PID loops, S-curve moves and G-code programs, and applies spring-damper forces for drones and swarms. Every tick it collects `sensordata` into a `SensorFrame`. Its carried state (`ControllerSnapshot`) is stored in every worldline row. |

## Determinism, seek and branch

- **Determinism:** Jolt runs on its single-threaded job system with `CROSS_PLATFORM_DETERMINISTIC`, and MuJoCo is deterministic. Two runs with the same inputs give bit-identical worldlines.
- **Seek:** every row holds MuJoCo's full integration state, and a complete Jolt snapshot is kept every 100 ticks. `seek(t)` restores the keyframe at or before `t`, replays only Jolt against the recorded MuJoCo states, then restores row `t`. Continuing from there reproduces the original run bit for bit.
- **Controller:** every row also holds a `ControllerSnapshot` (12 doubles per actuator plus 3, the `restore.control.*` columns):
  - commands: raw ctrl and setpoints;
  - PID gains, integrators and previous errors;
  - which program is running, when it started, and its segment cursor.

  Row `t` holds the controller as it enters tick `t + 1`; the block is refreshed when stepping resumes, so commands given between ticks belong to the row before them. A seek restores all of it and forgets programs that only the dropped future used, so giving the same commands again after a seek reproduces the original run.
- **Branch:** `branch(t)` builds an independent world at tick `t`. It gets a copy of this controller (programs, springs) restored to tick `t`, so a closed loop continues exactly; change its inputs for parallel parameter tests.

Springs are live inputs: they aren't recorded per tick.

## Rules

- No includes, paths or links into `app/`. DevOS will consume this library, never the other way round.
- Public headers use only the standard library, Eigen and forward-declared `mjModel_`/`mjData_`. Jolt types stay in `src/`.
- `EIGEN_MAX_ALIGN_BYTES=16` is part of the library's public interface. Jolt builds the library with `-mavx2`, and without this pin, callers built without AVX would disagree on the layout of `Pose`, `Vec3` and `Quat`.
- C++ files carry the DevOS header (FILE / OVERVIEW / PURPOSE / CONTRACT / NOTES with dated entries). `tools/check_standard.py` checks them.
