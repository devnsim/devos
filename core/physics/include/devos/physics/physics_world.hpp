// ==============================================================================
// FILE: core/physics/include/devos/physics/physics_world.hpp
// OVERVIEW: PhysicsWorld - the single entry point of the DevOS physics engine. Hides MuJoCo and Jolt behind
//           one API: load a scene, step it, reset it, seek back in time, branch it; read poses, sensors and
//           the worldline.
// PURPOSE:
//   1. Owns the master clock (Tick = fixed sub-steps at rate_hz; time = tick / rate_hz) and both solvers'
//      lifetimes; one canonical frame (right-handed, Z up, SI units, gravity -Z).
//   2. load_scene(path) (SceneLoader), step(dt) (whole sub-steps; remainders carry over), reset(), seek(tick)
//      (bit-exact: MuJoCo, Jolt via keyframes, and the controller - commands, PID integrators, program
//      cursor), branch(tick) (an independent copy from that tick, inheriting the controller's programs).
//   3. Records every tick into the WorldlineBuffer (layout in worldline_layout()).
// CONTRACT:
//   - Requires: types.hpp, worldline_buffer.hpp, controller_port.hpp, coupled_stepper.hpp, scene_loader.hpp.
//               No MuJoCo / Jolt types in this header.
//   - Exposes: PhysicsWorld(Config); WorldConfig; SceneSummary; load_scene(), step(), step_ticks(), reset(),
//              seek(), branch(), tick(), time(), pose(), body_names(), controller(), worldline(), summary(),
//              warnings(), coupling()
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] seek / branch restore the ControllerPort too (ControllerSnapshot in every row).
// ==============================================================================
#pragma once

#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "devos/physics/controller_port.hpp"
#include "devos/physics/coupled_stepper.hpp"
#include "devos/physics/scene_loader.hpp"
#include "devos/physics/types.hpp"
#include "devos/physics/worldline_buffer.hpp"

namespace devos::physics {

struct WorldConfig {
    double rate_hz = 1000.0;                   // sub-step rate shared by both solvers (overrides MJCF timestep)
    std::size_t worldline_capacity = 60000;    // rows kept (60 s at 1 kHz)
    Vec3 gravity = kGravity;
    CouplingSettings coupling;
};

struct SceneSummary {
    std::string source;
    int mujoco_bodies = 0, mujoco_joints = 0, actuators = 0, sensors = 0;
    std::size_t proxies = 0, static_bodies = 0, dynamic_bodies = 0;
    std::size_t worldline_width = 0;
};

class PhysicsWorld {
public:
    explicit PhysicsWorld(WorldConfig config = {});
    ~PhysicsWorld();
    PhysicsWorld(const PhysicsWorld&) = delete;
    PhysicsWorld& operator=(const PhysicsWorld&) = delete;

    void load_scene(const std::filesystem::path& path);
    bool is_loaded() const;

    std::size_t step(double dt);               // advances whole sub-steps covering dt; returns how many
    void step_ticks(std::size_t ticks);
    void reset();                              // back to tick 0 (the loaded state, current commands, loops
                                               // cleared); clears the worldline
    // Restore a recorded tick - solvers and controller (commands, PID state, program) as they entered the
    // next tick; the future after it is dropped.
    void seek(Tick tick);
    // A separate world continuing from `tick`, with a copy of this controller (programs, springs) restored
    // to that tick.
    std::unique_ptr<PhysicsWorld> branch(Tick tick) const;

    Tick tick() const;
    double time() const;
    double substep() const;
    std::optional<Pose> pose(const std::string& body) const; // MuJoCo body or Jolt dynamic body
    std::vector<std::string> body_names() const;             // tracked bodies, in worldline order

    ControllerPort& controller();
    const WorldlineBuffer& worldline() const;
    const WorldConfig& config() const;
    SceneSummary summary() const;
    const std::vector<std::string>& warnings() const;
    std::size_t contact_count() const;                       // proxy contacts in the last tick

    struct Impl;

private:
    std::unique_ptr<Impl> impl_;
};

}  // namespace devos::physics
