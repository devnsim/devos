// ==============================================================================
// FILE: core/physics/src/coupled_stepper.cpp
// OVERVIEW: The per-tick MuJoCo <-> Jolt handshake (order and force models in coupled_stepper.hpp).
// PURPOSE:
//   1. step(): wrench in -> controller -> mj_step -> mirror -> Jolt -> wrench out -> sensors.
//   2. compute_wrench(): contacts grouped per machine body; torque about the body's centre of mass (xipos).
// CONTRACT:
//   - Requires: coupled_stepper.hpp, jolt_world.hpp, MuJoCo
//   - Exposes: CoupledStepper member definitions
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
// ==============================================================================
#include "devos/physics/coupled_stepper.hpp"

#include <algorithm>
#include <map>

#include <mujoco/mujoco.h>

#include "jolt_world.hpp"

namespace devos::physics {

CoupledStepper::CoupledStepper(mjModel_* model, mjData_* data, detail::JoltWorld* jolt, ControllerPort* controller,
                               std::vector<int> proxy_bodies, CouplingSettings settings)
    : model_(model), data_(data), jolt_(jolt), controller_(controller), proxy_bodies_(std::move(proxy_bodies)),
      settings_(settings), wrench_(std::size_t(6 * model->nbody), 0.0) {}

double CoupledStepper::substep() const { return model_->opt.timestep; }

Pose CoupledStepper::body_pose(int body) const {
    return {Vec3(data_->xpos + 3 * body), mujoco_quat(data_->xquat + 4 * body)};
}

void CoupledStepper::mirror_now() {
    for (std::size_t p = 0; p < proxy_bodies_.size(); ++p) jolt_->place_proxy(p, body_pose(proxy_bodies_[p]));
}

void CoupledStepper::set_coupling_wrench(std::span<const double> wrench) {
    if (wrench.size() != wrench_.size()) throw PhysicsError("coupling wrench has the wrong size");
    std::copy(wrench.begin(), wrench.end(), wrench_.begin());
}

void CoupledStepper::step(Tick tick_after) {
    const double dt = substep();
    // 1-2. forces from the room (last tick), then the controller (ctrl + springs add to xfrc_applied)
    std::copy(wrench_.begin(), wrench_.end(), data_->xfrc_applied);
    controller_->apply(model_, data_, data_->time, dt);
    // 3. the machine
    mj_step(model_, data_);
    // mj_step leaves positions-derived quantities at the pre-step state; refresh what the mirror, the
    // coupling torques and the energies read (cheap compared with a full mj_forward).
    mj_kinematics(model_, data_);
    mj_comPos(model_, data_);
    mj_comVel(model_, data_);
    // 4. mirror the machine into the room
    for (std::size_t p = 0; p < proxy_bodies_.size(); ++p) jolt_->move_proxy(p, body_pose(proxy_bodies_[p]), dt);
    // 5. the room
    dynamic_velocity_before_.resize(jolt_->dynamic_count());
    for (std::size_t d = 0; d < jolt_->dynamic_count(); ++d) dynamic_velocity_before_[d] = jolt_->dynamic_state(d).linear_velocity;
    jolt_->step(dt);
    // 6. what the room did to the machine (applied next tick)
    compute_wrench();
    // 7. sensors
    controller_->gather(model_, data_, tick_after, data_->time);
}

void CoupledStepper::compute_wrench() {
    std::fill(wrench_.begin(), wrench_.end(), 0.0);
    const auto& contacts = jolt_->contacts();
    contact_count_ = contacts.size();
    const double dt = substep();
    const Vec3 gravity = jolt_->gravity();

    // Loose bodies touched only by proxies: their momentum change is the machine's push; react it back,
    // shared between the contacts by depth.
    std::map<int, double> depth_per_dynamic;
    for (const auto& contact : contacts)
        if (contact.dynamic_index >= 0) depth_per_dynamic[contact.dynamic_index] += contact.depth;
    std::map<int, Vec3> reaction_per_dynamic;
    for (const auto& [index, depth] : depth_per_dynamic) {
        if (jolt_->dynamic_touched_non_proxy(std::size_t(index))) continue;
        const Vec3 after = jolt_->dynamic_state(std::size_t(index)).linear_velocity;
        const Vec3 impulse = jolt_->dynamic_mass(std::size_t(index)) * (after - dynamic_velocity_before_[std::size_t(index)] - gravity * dt);
        reaction_per_dynamic[index] = -impulse / dt;
    }

    for (const auto& contact : contacts) {
        Vec3 force;
        if (contact.dynamic_index >= 0 && reaction_per_dynamic.count(contact.dynamic_index)) {
            const double share = contact.depth / std::max(depth_per_dynamic[contact.dynamic_index], 1e-12);
            force = share * reaction_per_dynamic[contact.dynamic_index];
        } else {
            const double separating = (contact.proxy_velocity - contact.other_velocity).dot(contact.push);
            const double magnitude = std::clamp(settings_.contact_stiffness * contact.depth - settings_.contact_damping * separating,
                                                0.0, settings_.max_force);
            force = magnitude * contact.push;
        }
        if (force.norm() > settings_.max_force) force *= settings_.max_force / force.norm();
        const int body = proxy_bodies_[std::size_t(contact.proxy)];
        const Vec3 arm = contact.point - Vec3(data_->xipos + 3 * body);
        const Vec3 torque = arm.cross(force);
        for (int i = 0; i < 3; ++i) {
            wrench_[std::size_t(6 * body + i)] += force[i];
            wrench_[std::size_t(6 * body + 3 + i)] += torque[i];
        }
    }
}

}  // namespace devos::physics
