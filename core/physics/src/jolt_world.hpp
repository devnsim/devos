// ==============================================================================
// FILE: core/physics/src/jolt_world.hpp
// OVERVIEW: Internal Jolt wrapper (not installed, not in the public API): one Jolt PhysicsSystem holding the
//           room (static + loose dynamic bodies) and kinematic proxies of the machine's bodies.
// PURPOSE:
//   1. Owns Jolt's process-wide setup (allocator, factory, type registry; reference counted), the layer
//      filters, a temp allocator and a SINGLE-THREADED job system (deterministic results run to run).
//   2. Object layers: STATIC (room), DYNAMIC (loose bodies), PROXY (machine twins). Proxies touch the room
//      but not each other (the machine's own collisions are MuJoCo's job).
//   3. Records, per step, every contact that involves a proxy (point, normal pushing the proxy out, depth,
//      point velocities) and which dynamic bodies touched anything that is not a proxy.
//   4. save_state() / restore_state(): Jolt's StateRecorder snapshots (keyframes for exact seek / branch).
// CONTRACT:
//   - Requires: Jolt Physics, scene_loader.hpp (ShapeDesc, EnvironmentBody, MachineProxy), types.hpp
//   - Exposes: devos::physics::detail::JoltWorld, ProxyContact, BodyState
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork. Jolt runs in single precision; the canonical
//                  frame is Z up (gravity set to -Z), identical to MuJoCo's, so poses copy across unchanged.
// ==============================================================================
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "devos/physics/scene_loader.hpp"
#include "devos/physics/types.hpp"

namespace devos::physics::detail {

struct ProxyContact {
    int proxy = -1;                    // index into the proxies (load order)
    Vec3 point = Vec3::Zero();         // world, on the proxy's surface
    Vec3 push = Vec3::Zero();          // unit normal that moves the proxy out of the contact
    double depth = 0;                  // penetration (m, > 0)
    Vec3 proxy_velocity = Vec3::Zero();
    Vec3 other_velocity = Vec3::Zero();
    int dynamic_index = -1;            // the other body if it is a loose dynamic body, else -1
};

struct BodyState {
    Vec3 position = Vec3::Zero();
    Quat orientation = Quat::Identity();
    Vec3 linear_velocity = Vec3::Zero();
    Vec3 angular_velocity = Vec3::Zero();
};

class JoltWorld {
public:
    explicit JoltWorld(const Vec3& gravity, unsigned max_bodies = 8192);
    ~JoltWorld();
    JoltWorld(const JoltWorld&) = delete;
    JoltWorld& operator=(const JoltWorld&) = delete;

    void add_environment(const EnvironmentBody& body);
    void add_proxy(const MachineProxy& proxy, const Pose& pose);
    void finish_loading();                                  // optimise the broad phase once everything is in

    void move_proxy(std::size_t index, const Pose& target, double dt);   // reaches target at the end of the step
    void place_proxy(std::size_t index, const Pose& pose);               // teleport (reset / seek)
    void step(double dt);

    std::size_t proxy_count() const;
    std::size_t dynamic_count() const;
    std::size_t static_count() const;
    const std::string& dynamic_name(std::size_t index) const;
    double dynamic_mass(std::size_t index) const;
    BodyState dynamic_state(std::size_t index) const;
    void set_dynamic_state(std::size_t index, const BodyState& state);
    double dynamic_kinetic_energy(std::size_t index) const;
    bool dynamic_touched_non_proxy(std::size_t index) const;            // during the last step

    const std::vector<ProxyContact>& contacts() const;                  // during the last step

    // Complete solver state (bodies, velocities, contact cache) as bytes: restoring it and stepping with the
    // same inputs reproduces Jolt bit for bit (keyframes for exact seeking).
    std::string save_state() const;
    void restore_state(const std::string& snapshot);
    Vec3 gravity() const;

    struct Impl;                                                        // Jolt types live in the .cpp only

private:
    std::unique_ptr<Impl> impl_;
};

}  // namespace devos::physics::detail
