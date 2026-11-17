// ==============================================================================
// FILE: core/physics/include/devos/physics/coupled_stepper.hpp
// OVERVIEW: CoupledStepper - keeps MuJoCo (the machine) and Jolt (the room) in lock-step at one fixed
//           sub-step (e.g. 1 kHz) and makes them feel each other.
// PURPOSE:
//   Every tick, in this order:
//   1. xfrc_applied = the coupling wrench found last tick (contact forces from the room);
//   2. ControllerPort::apply() writes ctrl (and spring forces);
//   3. mj_step() advances the machine;
//   4. machine body poses are mirrored into Jolt as kinematic proxies (MoveKinematic: they arrive exactly at
//      MuJoCo's pose at the end of the Jolt step, with matching velocity);
//   5. Jolt steps the room; contacts between proxies and the room are collected;
//   6. the contacts become the next coupling wrench (force at the body's centre of mass + torque):
//      - static fixtures: spring-damper penalty  F = max(0, k*depth - c*v_separation) along the push normal;
//      - loose bodies: the body's momentum change over the step (minus gravity) reacts on the machine when
//        it touched only proxies this step, otherwise the penalty model (an honest approximation; Jolt does
//        not report solved impulses to listeners);
//   7. ControllerPort::gather() reads the sensors.
//   The one-tick lag of the coupling is the standard explicit (staggered) coupling scheme.
// CONTRACT:
//   - Requires: types.hpp, controller_port.hpp; MuJoCo via forward-declared mjModel_ / mjData_; the internal
//               detail::JoltWorld (forward-declared).
//   - Exposes: CouplingSettings, CoupledStepper(model, data, jolt, controller, proxies, settings) with step(),
//              substep(), coupling_wrench() / set_coupling_wrench() (restore), mirror_now(), contact_count()
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
// ==============================================================================
#pragma once

#include <cstddef>
#include <span>
#include <vector>

#include "devos/physics/controller_port.hpp"
#include "devos/physics/types.hpp"

struct mjModel_;
struct mjData_;

namespace devos::physics {
namespace detail { class JoltWorld; }

struct CouplingSettings {
    double contact_stiffness = 2.0e4;     // N/m, penalty against static fixtures
    double contact_damping = 2.0e2;       // N s/m
    double max_force = 5.0e4;             // N, per contact (keeps a deep first contact from exploding)
};

class CoupledStepper {
public:
    CoupledStepper(mjModel_* model, mjData_* data, detail::JoltWorld* jolt, ControllerPort* controller,
                   std::vector<int> proxy_bodies, CouplingSettings settings);

    void step(Tick tick_after);                      // one sub-step of model->opt.timestep
    double substep() const;
    void mirror_now();                               // teleport the proxies to MuJoCo's current poses
    std::span<const double> coupling_wrench() const { return wrench_; }   // 6 per MuJoCo body
    void set_coupling_wrench(std::span<const double> wrench);
    std::size_t contact_count() const { return contact_count_; }
    const CouplingSettings& settings() const { return settings_; }

private:
    Pose body_pose(int body) const;
    void compute_wrench();

    mjModel_* model_;
    mjData_* data_;
    detail::JoltWorld* jolt_;
    ControllerPort* controller_;
    std::vector<int> proxy_bodies_;                  // proxy index -> MuJoCo body id
    CouplingSettings settings_;
    std::vector<double> wrench_;
    std::vector<Vec3> dynamic_velocity_before_;
    std::size_t contact_count_ = 0;
};

}  // namespace devos::physics
