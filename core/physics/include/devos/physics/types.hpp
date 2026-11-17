// ==============================================================================
// FILE: core/physics/include/devos/physics/types.hpp
// OVERVIEW: Shared vocabulary of the DevOS physics engine: the canonical frame, poses, ticks, errors.
// PURPOSE:
//   1. One coordinate convention for everything: right-handed, Z up, metres, kilograms, seconds,
//      gravity along -Z. MuJoCo uses it natively; Jolt is set up to match (gravity -Z), so no axis swaps.
//   2. Small value types used by every component (Pose, Tick, Solver, PhysicsError).
// CONTRACT:
//   - Requires: Eigen3 (header only), C++20. No MuJoCo / Jolt / Python / Qt types.
//   - Exposes: devos::physics::{Vec3, Quat, Pose, Tick, Solver, PhysicsError, kGravity, mujoco_quat()}
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork (core/physics).
// ==============================================================================
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

#include <Eigen/Geometry>

namespace devos::physics {

using Vec3 = Eigen::Vector3d;
using Quat = Eigen::Quaterniond;      // Eigen stores (x, y, z, w); construct with Quat(w, x, y, z)
using Tick = std::uint64_t;           // sub-step counter since reset (0 = initial state)

inline const Vec3 kGravity{0.0, 0.0, -9.81};

struct Pose {
    Vec3 position = Vec3::Zero();
    Quat orientation = Quat::Identity();
};

enum class Solver : std::uint8_t { MuJoCo, Jolt };

struct PhysicsError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// MuJoCo stores quaternions as (w, x, y, z).
inline Quat mujoco_quat(const double* wxyz) { return Quat(wxyz[0], wxyz[1], wxyz[2], wxyz[3]); }

}  // namespace devos::physics
