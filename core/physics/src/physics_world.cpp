// ==============================================================================
// FILE: core/physics/src/physics_world.cpp
// OVERVIEW: PhysicsWorld implementation: scene setup, the master clock, worldline recording, reset / seek /
//           branch.
// PURPOSE:
//   1. load_scene(): SceneLoader -> MuJoCo model + data (timestep = 1 / rate_hz, gravity, energy on) and a
//      JoltWorld with the room + machine proxies; a scene without machine.xml gets an empty MuJoCo model.
//   2. Worldline row = observation block (time, tick, poses, joint torques, coupling forces, energies) +
//      restore block (MuJoCo integration state, Jolt loose-body states, pending coupling wrench, controller
//      snapshot). The controller block of row t holds the controller as it enters tick t + 1: it's written
//      with the row and refreshed when stepping resumes, so commands given between ticks belong to the row
//      before them and a seek + re-run reproduces the run.
//   3. Exact seek: a full Jolt snapshot is kept every kKeyframeTicks ticks. seek(t) restores the keyframe at
//      or before t, replays only Jolt up to t against the recorded MuJoCo states (every row holds MuJoCo's
//      exact integration state, so MuJoCo needs no re-simulation), then restores row t. Result: the same
//      bits as the original run. Without a keyframe in the window it falls back to restoring body states
//      (Jolt's contact cache then starts cold).
// CONTRACT:
//   - Requires: physics_world.hpp, jolt_world.hpp, MuJoCo
//   - Exposes: PhysicsWorld member definitions
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] Controller snapshot in the restore block: seek / branch restore commands, PID integrators
//                  and the program cursor instead of restarting the loops; a branch inherits the controller's
//                  programs and settings; programs only the dropped future used are forgotten on seek.
// ==============================================================================
#include "devos/physics/physics_world.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <map>

#include <mujoco/mujoco.h>

#include "jolt_world.hpp"

namespace devos::physics {
namespace {

constexpr unsigned kStateSpec = mjSTATE_INTEGRATION;
constexpr std::size_t kBodyColumns = 7;          // px py pz qw qx qy qz
constexpr std::size_t kDynamicStateColumns = 13; // p(3) q(4) v(3) w(3)
constexpr Tick kKeyframeTicks = 100;             // a Jolt snapshot every 100 ticks (0.1 s at 1 kHz)

std::shared_ptr<mjModel> empty_model() {
    static const char* xml = "<mujoco model=\"empty\"><worldbody/></mujoco>";
    mjVFS vfs;
    mj_defaultVFS(&vfs);
    mj_addBufferVFS(&vfs, "empty.xml", xml, int(std::strlen(xml)));
    std::array<char, 512> error{};
    mjModel* model = mj_loadXML("empty.xml", &vfs, error.data(), int(error.size()));
    mj_deleteVFS(&vfs);
    if (!model) throw PhysicsError(std::string("empty MuJoCo model: ") + error.data());
    return {model, [](mjModel* doomed) { mj_deleteModel(doomed); }};
}

std::string object_name(const mjModel* model, mjtObj type, int id, const char* fallback) {
    const char* name = mj_id2name(model, type, id);
    return name ? name : std::string(fallback) + std::to_string(id);
}

void put_pose(double* out, const Vec3& position, const Quat& orientation) {
    out[0] = position.x(); out[1] = position.y(); out[2] = position.z();
    out[3] = orientation.w(); out[4] = orientation.x(); out[5] = orientation.y(); out[6] = orientation.z();
}

}  // namespace

struct PhysicsWorld::Impl {
    WorldConfig config;
    SceneBundle bundle;
    std::shared_ptr<mjModel> model;
    mjData* data = nullptr;
    std::unique_ptr<detail::JoltWorld> jolt;
    ControllerPort controller;
    std::unique_ptr<CoupledStepper> stepper;
    WorldlineBuffer worldline;
    std::string initial_snapshot;                       // Jolt right after loading (reset)
    std::map<Tick, std::string> keyframes;              // tick -> Jolt snapshot
    std::vector<int> proxy_bodies;
    Tick tick = 0;
    double carry = 0;                                   // unstepped remainder of step(dt)
    std::size_t state_size = 0;
    std::size_t restore_dynamic_offset = 0, restore_wrench_offset = 0, restore_control_offset = 0;

    ~Impl() { unload(); }

    void unload() {
        stepper.reset();
        jolt.reset();
        if (data) mj_deleteData(data);
        data = nullptr;
        model.reset();
    }

    void require_loaded() const {
        if (!data) throw PhysicsError("no scene loaded (call load_scene first)");
    }

    WorldlineLayout make_layout() {
        WorldlineLayout layout;
        auto& columns = layout.columns;
        columns = {"time", "tick"};
        auto add_pose = [&](const std::string& name) {
            for (const char* part : {"px", "py", "pz", "qw", "qx", "qy", "qz"}) columns.push_back(name + "." + part);
        };
        for (int b = 1; b < model->nbody; ++b) add_pose(object_name(model.get(), mjOBJ_BODY, b, "body"));
        for (std::size_t d = 0; d < jolt->dynamic_count(); ++d) add_pose(jolt->dynamic_name(d));
        for (int dof = 0; dof < model->nv; ++dof) {
            const int joint = model->dof_jntid[dof];
            const int first = model->jnt_dofadr[joint];
            std::string name = "tau." + object_name(model.get(), mjOBJ_JOINT, joint, "joint");
            if (dof - first > 0 || model->jnt_type[joint] == mjJNT_FREE || model->jnt_type[joint] == mjJNT_BALL)
                name += "[" + std::to_string(dof - first) + "]";
            columns.push_back(name);
        }
        for (int body : proxy_bodies)
            for (const char* part : {"fx", "fy", "fz"})
                columns.push_back("contact." + object_name(model.get(), mjOBJ_BODY, body, "body") + "." + part);
        columns.push_back("kinetic_energy");
        columns.push_back("potential_energy");
        layout.observation_width = columns.size();
        layout.restore_offset = columns.size();
        state_size = std::size_t(mj_stateSize(model.get(), kStateSpec));
        for (std::size_t i = 0; i < state_size; ++i) columns.push_back("restore.mujoco[" + std::to_string(i) + "]");
        restore_dynamic_offset = columns.size();
        for (std::size_t d = 0; d < jolt->dynamic_count(); ++d)
            for (std::size_t i = 0; i < kDynamicStateColumns; ++i)
                columns.push_back("restore." + jolt->dynamic_name(d) + "[" + std::to_string(i) + "]");
        restore_wrench_offset = columns.size();
        for (int i = 0; i < 6 * model->nbody; ++i) columns.push_back("restore.wrench[" + std::to_string(i) + "]");
        restore_control_offset = columns.size();
        for (const auto& name : ControllerSnapshot::column_names(controller.actuator_names())) columns.push_back("restore." + name);
        return layout;
    }

    void record() {
        std::span<double> row = worldline.write_row(tick);
        std::size_t column = 0;
        row[column++] = data->time;
        row[column++] = double(tick);
        for (int b = 1; b < model->nbody; ++b, column += kBodyColumns)
            put_pose(&row[column], Vec3(data->xpos + 3 * b), mujoco_quat(data->xquat + 4 * b));
        double kinetic = 0, potential = 0;
        const Vec3 gravity = jolt->gravity();
        for (std::size_t d = 0; d < jolt->dynamic_count(); ++d, column += kBodyColumns) {
            const detail::BodyState state = jolt->dynamic_state(d);
            put_pose(&row[column], state.position, state.orientation);
            kinetic += jolt->dynamic_kinetic_energy(d);
            potential -= jolt->dynamic_mass(d) * gravity.dot(state.position);
        }
        for (int dof = 0; dof < model->nv; ++dof) row[column++] = data->qfrc_actuator[dof];
        const auto wrench = stepper->coupling_wrench();
        for (int body : proxy_bodies)
            for (int i = 0; i < 3; ++i) row[column++] = wrench[std::size_t(6 * body + i)];
        mj_energyPos(model.get(), data);
        mj_energyVel(model.get(), data);
        row[column++] = kinetic + data->energy[1];
        row[column++] = potential + data->energy[0];

        mj_getState(model.get(), data, &row[column], kStateSpec);
        column += state_size;
        for (std::size_t d = 0; d < jolt->dynamic_count(); ++d, column += kDynamicStateColumns) {
            const detail::BodyState state = jolt->dynamic_state(d);
            put_pose(&row[column], state.position, state.orientation);
            for (int i = 0; i < 3; ++i) {
                row[column + 7 + std::size_t(i)] = state.linear_velocity[i];
                row[column + 10 + std::size_t(i)] = state.angular_velocity[i];
            }
        }
        std::copy(wrench.begin(), wrench.end(), row.begin() + std::ptrdiff_t(column));
        controller.snapshot().write(control_block(row));

        if (tick % kKeyframeTicks == 0) keyframes[tick] = jolt->save_state();
        while (!keyframes.empty() && keyframes.begin()->first < worldline.oldest_tick()) keyframes.erase(keyframes.begin());
    }

    std::span<double> control_block(std::span<double> row) const {
        return row.subspan(restore_control_offset, ControllerSnapshot::width(std::size_t(model->nu)));
    }

    // Commands given since the newest row was written belong to it (they drive the next tick).
    void refresh_control() { controller.snapshot().write(control_block(worldline.update_row(tick))); }

    // After the future is dropped: programs only the dropped future used are forgotten, so a program given
    // again after a seek gets the same index (and the same row bits) as the first time.
    void trim_programs() {
        const std::size_t column = restore_control_offset + ControllerSnapshot::width(std::size_t(model->nu)) -
                                   ControllerSnapshot::kProgramWidth;
        double newest = -1;
        for (Tick i = worldline.oldest_tick(); i <= worldline.newest_tick(); ++i)
            newest = std::max(newest, worldline.row(i)[column]);
        controller.forget_programs_after(int(std::lround(newest)));
    }

    // MuJoCo from row (exact); Jolt from row body states only when set_jolt (no keyframe path).
    void restore(std::span<const double> row, bool set_jolt) {
        const WorldlineLayout& layout = worldline.layout();
        mj_setState(model.get(), data, &row[layout.restore_offset], kStateSpec);
        mj_forward(model.get(), data);
        stepper->set_coupling_wrench(row.subspan(restore_wrench_offset, std::size_t(6 * model->nbody)));
        tick = Tick(std::llround(row[WorldlineLayout::kTickColumn]));
        carry = 0;
        controller.restore(ControllerSnapshot::read(
            row.subspan(restore_control_offset, ControllerSnapshot::width(std::size_t(model->nu))), std::size_t(model->nu)));
        if (!set_jolt) return;
        for (std::size_t d = 0; d < jolt->dynamic_count(); ++d) {
            const double* values = &row[restore_dynamic_offset + d * kDynamicStateColumns];
            detail::BodyState state;
            state.position = Vec3(values);
            state.orientation = Quat(values[3], values[4], values[5], values[6]);
            state.linear_velocity = Vec3(values + 7);
            state.angular_velocity = Vec3(values + 10);
            jolt->set_dynamic_state(d, state);
        }
        stepper->mirror_now();
    }

    // The newest keyframe at or before `target` that is still inside the worldline window.
    std::map<Tick, std::string>::const_iterator keyframe_for(Tick target) const {
        auto found = keyframes.upper_bound(target);
        while (found != keyframes.begin()) {
            --found;
            if (worldline.has_tick(found->first)) return found;
        }
        return keyframes.end();
    }

    void seek_exact(Tick target) {
        const auto keyframe = keyframe_for(target);
        if (keyframe == keyframes.end()) {
            restore(worldline.row(target), true);
            return;
        }
        jolt->restore_state(keyframe->second);
        const double dt = model->opt.timestep;
        const std::size_t offset = worldline.layout().restore_offset;
        for (Tick i = keyframe->first + 1; i <= target; ++i) {        // replay the room against the record
            mj_setState(model.get(), data, &worldline.row(i)[offset], kStateSpec);
            mj_kinematics(model.get(), data);
            for (std::size_t p = 0; p < proxy_bodies.size(); ++p)
                jolt->move_proxy(p, {Vec3(data->xpos + 3 * proxy_bodies[p]), mujoco_quat(data->xquat + 4 * proxy_bodies[p])}, dt);
            jolt->step(dt);
        }
        restore(worldline.row(target), false);
    }
};

// ---------------------------------------------------------------- public API
PhysicsWorld::PhysicsWorld(WorldConfig config) : impl_(std::make_unique<Impl>()) {
    if (config.rate_hz <= 0) throw PhysicsError("rate_hz must be positive");
    impl_->config = config;
}

PhysicsWorld::~PhysicsWorld() = default;

void PhysicsWorld::load_scene(const std::filesystem::path& path) {
    Impl& w = *impl_;
    SceneBundle bundle = SceneLoader::load(path);
    w.unload();
    w.bundle = std::move(bundle);
    w.model = w.bundle.machine ? w.bundle.machine : empty_model();
    mjModel* model = w.model.get();
    model->opt.timestep = 1.0 / w.config.rate_hz;
    for (int i = 0; i < 3; ++i) model->opt.gravity[i] = w.config.gravity[i];
    model->opt.enableflags |= mjENBL_ENERGY;
    w.data = mj_makeData(model);
    mj_forward(model, w.data);

    w.jolt = std::make_unique<detail::JoltWorld>(w.config.gravity);
    for (const auto& body : w.bundle.environment) w.jolt->add_environment(body);
    w.proxy_bodies.clear();
    for (const auto& proxy : w.bundle.proxies) {
        w.jolt->add_proxy(proxy, {Vec3(w.data->xpos + 3 * proxy.mujoco_body), mujoco_quat(w.data->xquat + 4 * proxy.mujoco_body)});
        w.proxy_bodies.push_back(proxy.mujoco_body);
    }
    w.jolt->finish_loading();
    w.initial_snapshot = w.jolt->save_state();
    w.keyframes.clear();

    w.controller.bind(model);
    w.stepper = std::make_unique<CoupledStepper>(model, w.data, w.jolt.get(), &w.controller, w.proxy_bodies, w.config.coupling);
    w.worldline = WorldlineBuffer(w.make_layout(), w.config.worldline_capacity);
    w.tick = 0;
    w.carry = 0;
    w.record();
}

bool PhysicsWorld::is_loaded() const { return impl_->data != nullptr; }

std::size_t PhysicsWorld::step(double dt) {
    impl_->require_loaded();
    if (dt < 0) throw PhysicsError("step(dt) needs dt >= 0 (use seek to go back)");
    impl_->carry += dt;
    const double h = substep();
    const auto ticks = std::size_t(std::floor(impl_->carry / h + 1e-9));
    impl_->carry = std::max(0.0, impl_->carry - double(ticks) * h);
    step_ticks(ticks);
    return ticks;
}

void PhysicsWorld::step_ticks(std::size_t ticks) {
    Impl& w = *impl_;
    w.require_loaded();
    if (ticks > 0) w.refresh_control();
    for (std::size_t i = 0; i < ticks; ++i) {
        w.stepper->step(w.tick + 1);
        ++w.tick;
        w.record();
    }
}

void PhysicsWorld::reset() {
    Impl& w = *impl_;
    w.require_loaded();
    mj_resetData(w.model.get(), w.data);
    mj_forward(w.model.get(), w.data);
    w.jolt->restore_state(w.initial_snapshot);
    std::vector<double> zero(std::size_t(6 * w.model->nbody), 0.0);
    w.stepper->set_coupling_wrench(zero);
    w.controller.reset_loop_state();
    w.tick = 0;
    w.carry = 0;
    w.worldline.clear();
    w.keyframes.clear();
    w.record();
}

void PhysicsWorld::seek(Tick tick) {
    Impl& w = *impl_;
    w.require_loaded();
    if (!w.worldline.has_tick(tick)) (void)w.worldline.row(tick);    // throws a readable error
    w.seek_exact(tick);
    w.worldline.truncate_after(tick);
    w.keyframes.erase(w.keyframes.upper_bound(tick), w.keyframes.end());
    w.trim_programs();
}

std::unique_ptr<PhysicsWorld> PhysicsWorld::branch(Tick tick) const {
    const Impl& w = *impl_;
    w.require_loaded();
    (void)w.worldline.row(tick);                                       // throws if outside the window
    auto copy = std::make_unique<PhysicsWorld>(w.config);
    copy->load_scene(w.bundle.source);
    Impl& c = *copy->impl_;
    c.controller = w.controller;                   // programs, gains, springs; the row restores the rest
    c.controller.on_sensor_frame(nullptr);
    c.controller.bind(c.model.get());
    // Hand over the history from the keyframe (or just the tick) and seek there: an exact continuation.
    const auto keyframe = w.keyframe_for(tick);
    const Tick first = keyframe != w.keyframes.end() ? keyframe->first : tick;
    c.worldline.clear();
    c.keyframes.clear();
    for (Tick i = first; i <= tick; ++i) {
        const auto source = w.worldline.row(i);
        std::copy(source.begin(), source.end(), c.worldline.write_row(i).begin());
    }
    if (keyframe != w.keyframes.end()) c.keyframes[keyframe->first] = keyframe->second;
    c.seek_exact(tick);
    c.trim_programs();
    return copy;
}

Tick PhysicsWorld::tick() const { return impl_->tick; }
double PhysicsWorld::time() const { return impl_->data ? impl_->data->time : 0.0; }
double PhysicsWorld::substep() const { return 1.0 / impl_->config.rate_hz; }

std::optional<Pose> PhysicsWorld::pose(const std::string& body) const {
    const Impl& w = *impl_;
    if (!w.data) return std::nullopt;
    const int id = mj_name2id(w.model.get(), mjOBJ_BODY, body.c_str());
    if (id >= 0) return Pose{Vec3(w.data->xpos + 3 * id), mujoco_quat(w.data->xquat + 4 * id)};
    for (std::size_t d = 0; d < w.jolt->dynamic_count(); ++d)
        if (w.jolt->dynamic_name(d) == body) {
            const auto state = w.jolt->dynamic_state(d);
            return Pose{state.position, state.orientation};
        }
    return std::nullopt;
}

std::vector<std::string> PhysicsWorld::body_names() const {
    const Impl& w = *impl_;
    std::vector<std::string> names;
    if (!w.data) return names;
    for (int b = 1; b < w.model->nbody; ++b) names.push_back(object_name(w.model.get(), mjOBJ_BODY, b, "body"));
    for (std::size_t d = 0; d < w.jolt->dynamic_count(); ++d) names.push_back(w.jolt->dynamic_name(d));
    return names;
}

ControllerPort& PhysicsWorld::controller() { return impl_->controller; }
const WorldlineBuffer& PhysicsWorld::worldline() const { return impl_->worldline; }
const WorldConfig& PhysicsWorld::config() const { return impl_->config; }
const std::vector<std::string>& PhysicsWorld::warnings() const { return impl_->bundle.warnings; }
std::size_t PhysicsWorld::contact_count() const { return impl_->stepper ? impl_->stepper->contact_count() : 0; }

SceneSummary PhysicsWorld::summary() const {
    const Impl& w = *impl_;
    SceneSummary summary;
    if (!w.data) return summary;
    summary.source = w.bundle.source.string();
    summary.mujoco_bodies = w.model->nbody - 1;
    summary.mujoco_joints = w.model->njnt;
    summary.actuators = w.model->nu;
    summary.sensors = w.model->nsensor;
    summary.proxies = w.jolt->proxy_count();
    summary.static_bodies = w.jolt->static_count();
    summary.dynamic_bodies = w.jolt->dynamic_count();
    summary.worldline_width = w.worldline.width();
    return summary;
}

}  // namespace devos::physics
