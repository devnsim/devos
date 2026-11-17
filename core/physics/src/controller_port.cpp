// ==============================================================================
// FILE: core/physics/src/controller_port.cpp
// OVERVIEW: ControllerPort, S-curve moves, trajectories and the G-code reader.
// PURPOSE:
//   1. SCurve: minimum-jerk quintic s(u) = 10u^3 - 15u^4 + 6u^5; peak velocity 1.875 d/T and peak
//      acceleration 5.774 d/T^2 decide the duration T from the limits.
//   2. GcodeProgram: a small, strict reader (unknown words are ignored, comments in ( ) or after ;).
//   3. ControllerPort::apply(): raw ctrl > PID on setpoint > setpoint as ctrl; clamped to ctrlrange;
//      springs add to xfrc_applied. gather(): sensordata by sensor name, ctrl, actuator forces.
//   4. ControllerSnapshot: per actuator 12 doubles (raw?, raw, setpoint?, setpoint, pid?, kp, ki, kd,
//      integral_limit, integral, previous_error, previous?) + 3 for the program (index, start, segment).
// CONTRACT:
//   - Requires: controller_port.hpp, MuJoCo (mujoco/mujoco.h)
//   - Exposes: the definitions declared in controller_port.hpp
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] ControllerSnapshot encode / decode, snapshot() / restore(); programs list + segment cursor
//                  (Trajectory::sample(t, cursor), held end values per segment).
// ==============================================================================
#include "devos/physics/controller_port.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <sstream>

#include <mujoco/mujoco.h>

namespace devos::physics {

// ---------------------------------------------------------------- S-curve
SCurve SCurve::plan(double start, double end, double max_velocity, double max_acceleration) {
    const double distance = std::abs(end - start);
    double duration = 0;
    if (distance > 0) {
        if (max_velocity <= 0 || max_acceleration <= 0) throw PhysicsError("S-curve limits must be positive");
        duration = std::max(1.875 * distance / max_velocity, std::sqrt(5.7735026919 * distance / max_acceleration));
    }
    return {start, end, duration};
}

double SCurve::position(double t) const {
    if (duration <= 0 || t >= duration) return end;
    if (t <= 0) return start;
    const double u = t / duration;
    return start + (end - start) * u * u * u * (10 - 15 * u + 6 * u * u);
}

double SCurve::velocity(double t) const {
    if (duration <= 0 || t <= 0 || t >= duration) return 0;
    const double u = t / duration;
    return (end - start) / duration * 30 * u * u * (1 - u) * (1 - u);
}

// ---------------------------------------------------------------- trajectory
void Trajectory::add_segment(Segment segment) {
    segment.start_time = duration();
    held_.push_back(final_);                                  // what earlier segments left behind
    for (const auto& [name, curve] : segment.axes) final_[name] = curve.end;
    segments_.push_back(std::move(segment));
}

double Trajectory::duration() const {
    return segments_.empty() ? 0.0 : segments_.back().start_time + segments_.back().duration;
}

std::map<std::string, double> Trajectory::sample(double t) const {
    std::size_t cursor = 0;
    return sample(t, cursor);
}

std::map<std::string, double> Trajectory::sample(double t, std::size_t& cursor) const {
    if (segments_.empty() || t >= duration()) {
        cursor = segments_.empty() ? 0 : segments_.size() - 1;
        return final_;
    }
    if (t < 0) return {};
    cursor = std::min(cursor, segments_.size() - 1);
    while (cursor > 0 && t < segments_[cursor].start_time) --cursor;            // only after a jump back
    while (cursor + 1 < segments_.size() && t >= segments_[cursor + 1].start_time) ++cursor;
    const Segment& segment = segments_[cursor];
    std::map<std::string, double> values = held_[cursor];   // later segments override earlier ones
    for (const auto& [name, curve] : segment.axes) values[name] = curve.position(t - segment.start_time);
    return values;
}

// ---------------------------------------------------------------- G-code
GcodeProgram GcodeProgram::parse(const std::string& text) {
    GcodeProgram program;
    std::istringstream lines(text);
    std::string line;
    bool is_absolute = true;
    double unit_mm = 1.0;
    double feed = 0;
    std::map<char, double> position;
    int line_number = 0;
    while (std::getline(lines, line)) {
        ++line_number;
        std::string clean;
        int depth = 0;
        for (char c : line) {                                   // strip ( comments ) and ; comments
            if (c == ';') break;
            if (c == '(') { ++depth; continue; }
            if (c == ')') { depth = std::max(0, depth - 1); continue; }
            if (depth == 0) clean += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
        std::map<char, double> words;
        std::vector<int> g_codes;
        for (std::size_t i = 0; i < clean.size();) {
            char letter = clean[i];
            if (!std::isalpha(static_cast<unsigned char>(letter))) { ++i; continue; }
            std::size_t used = 0;
            double value = 0;
            try {
                value = std::stod(clean.substr(i + 1), &used);
            } catch (const std::exception&) {
                throw PhysicsError("G-code line " + std::to_string(line_number) + ": '" + letter + "' has no number");
            }
            if (letter == 'G') g_codes.push_back(static_cast<int>(std::lround(value)));
            else words[letter] = value;
            i += 1 + used;
        }
        int motion = -1;
        for (int code : g_codes) {
            if (code == 20) unit_mm = 25.4;
            else if (code == 21) unit_mm = 1.0;
            else if (code == 90) is_absolute = true;
            else if (code == 91) is_absolute = false;
            else if (code == 0 || code == 1 || code == 4) motion = code;
        }
        if (words.count('F')) feed = words['F'] * unit_mm;
        if (motion == 4) {
            Move dwell;
            dwell.dwell_s = words.count('P') ? words['P'] / 1000.0 : words.count('S') ? words['S'] : 0.0;
            program.moves_.push_back(dwell);
            continue;
        }
        if (motion == 0 || motion == 1) {
            Move move;
            move.is_rapid = motion == 0;
            move.feed = feed;
            for (char axis : {'X', 'Y', 'Z', 'A', 'B', 'C'}) {
                if (!words.count(axis)) continue;
                double value = words[axis] * unit_mm;
                position[axis] = is_absolute ? value : position[axis] + value;
                move.target[axis] = position[axis];
            }
            if (!move.target.empty()) program.moves_.push_back(move);
        }
    }
    return program;
}

Trajectory GcodeProgram::to_trajectory(const std::map<char, AxisBinding>& axes, const std::map<char, double>& start,
                                       double rapid_feed, double max_acceleration) const {
    Trajectory trajectory;
    std::map<char, double> position = start;                    // G-code units (mm)
    for (const auto& [axis, binding] : axes) position.try_emplace(axis, 0.0);
    for (const Move& move : moves_) {
        Trajectory::Segment segment;
        if (move.dwell_s > 0) {
            segment.duration = move.dwell_s;
            for (const auto& [axis, binding] : axes) {
                double value = position[axis] * binding.scale + binding.offset;
                segment.axes[binding.actuator] = SCurve{value, value, move.dwell_s};
            }
            trajectory.add_segment(segment);
            continue;
        }
        double length2 = 0;
        for (const auto& [axis, target] : move.target) length2 += (target - position[axis]) * (target - position[axis]);
        const double length = std::sqrt(length2);
        if (length <= 0) continue;
        const double feed = move.is_rapid ? rapid_feed : (move.feed > 0 ? move.feed : rapid_feed);
        const SCurve path = SCurve::plan(0.0, length, feed / 60.0, max_acceleration);
        segment.duration = path.duration;
        for (const auto& [axis, binding] : axes) {
            const double from = position[axis];
            const double to = move.target.count(axis) ? move.target.at(axis) : from;
            segment.axes[binding.actuator] = SCurve{from * binding.scale + binding.offset, to * binding.scale + binding.offset,
                                                    path.duration};
            position[axis] = to;
        }
        trajectory.add_segment(segment);
    }
    return trajectory;
}

// ---------------------------------------------------------------- inputs
void ControllerPort::set_ctrl(const std::string& actuator, double value) { raw_ctrl_[actuator] = value; }

void ControllerPort::set_setpoint(const std::string& actuator, double target) {
    raw_ctrl_.erase(actuator);
    setpoints_[actuator] = target;
}

void ControllerPort::set_pid(const std::string& actuator, PidGains gains) { loops_[actuator] = Loop{gains}; }
void ControllerPort::clear_pid(const std::string& actuator) { loops_.erase(actuator); }

void ControllerPort::run_trajectory(Trajectory trajectory, double start_time) {
    for (const auto& segment : trajectory.segments())
        for (const auto& [name, curve] : segment.axes) raw_ctrl_.erase(name);
    programs_.push_back(std::make_shared<const Trajectory>(std::move(trajectory)));
    program_ = static_cast<int>(programs_.size()) - 1;
    program_start_ = start_time;
    cursor_ = 0;
}

void ControllerPort::stop_trajectory() {
    program_ = -1;
    cursor_ = 0;
}

bool ControllerPort::trajectory_active(double time) const {
    return program_ >= 0 && time - program_start_ < programs_[static_cast<std::size_t>(program_)]->duration();
}

int ControllerPort::add_spring(const std::string& body, const Vec3& anchor, double stiffness, double damping, double rest_length) {
    springs_.push_back({-1, body, anchor, stiffness, damping, rest_length});
    return static_cast<int>(springs_.size()) - 1;
}

void ControllerPort::move_spring_anchor(int spring, const Vec3& anchor) {
    if (spring < 0 || spring >= static_cast<int>(springs_.size())) throw PhysicsError("no spring " + std::to_string(spring));
    springs_[static_cast<std::size_t>(spring)].anchor = anchor;
}

void ControllerPort::remove_springs() { springs_.clear(); }

// ---------------------------------------------------------------- stepper side
int ControllerPort::actuator_index(const std::string& name) const {
    auto found = std::find(actuator_names_.begin(), actuator_names_.end(), name);
    return found == actuator_names_.end() ? -1 : static_cast<int>(found - actuator_names_.begin());
}

void ControllerPort::bind(const mjModel_* model) {
    actuator_names_.clear();
    actuator_qpos_.clear();
    actuator_dof_.clear();
    for (int a = 0; a < model->nu; ++a) {
        const char* name = mj_id2name(model, mjOBJ_ACTUATOR, a);
        actuator_names_.push_back(name ? name : "actuator" + std::to_string(a));
        const bool is_joint = model->actuator_trntype[a] == mjTRN_JOINT;
        const int joint = model->actuator_trnid[2 * a];
        actuator_qpos_.push_back(is_joint ? model->jnt_qposadr[joint] : -1);
        actuator_dof_.push_back(is_joint ? model->jnt_dofadr[joint] : -1);
    }
    for (auto& spring : springs_) {
        spring.body = mj_name2id(model, mjOBJ_BODY, spring.body_name.c_str());
        if (spring.body < 0) throw PhysicsError("spring body '" + spring.body_name + "' not in the model");
    }
    reset_loop_state();
}

void ControllerPort::reset_loop_state() {
    for (auto& [name, loop] : loops_) {
        loop.integral = 0;
        loop.has_previous = false;
    }
    cursor_ = 0;
}

ControllerSnapshot ControllerPort::snapshot() const {
    ControllerSnapshot snapshot;
    for (const std::string& name : actuator_names_) {
        ControllerSnapshot::Channel channel;
        if (auto raw = raw_ctrl_.find(name); raw != raw_ctrl_.end()) {
            channel.has_raw = true;
            channel.raw = raw->second;
        }
        if (auto setpoint = setpoints_.find(name); setpoint != setpoints_.end()) {
            channel.has_setpoint = true;
            channel.setpoint = setpoint->second;
        }
        if (auto loop = loops_.find(name); loop != loops_.end()) {
            channel.has_pid = true;
            channel.gains = loop->second.gains;
            channel.integral = loop->second.integral;
            channel.previous_error = loop->second.previous_error;
            channel.has_previous = loop->second.has_previous;
        }
        snapshot.channels.push_back(channel);
    }
    snapshot.program = program_;
    snapshot.program_start = program_start_;
    snapshot.segment = cursor_;
    return snapshot;
}

void ControllerPort::restore(const ControllerSnapshot& snapshot) {
    if (snapshot.channels.size() != actuator_names_.size())
        throw PhysicsError("controller snapshot has " + std::to_string(snapshot.channels.size()) + " channels, the model " +
                           std::to_string(actuator_names_.size()) + " actuators");
    if (snapshot.program >= static_cast<int>(programs_.size()))
        throw PhysicsError("controller snapshot names program " + std::to_string(snapshot.program) + ", only " +
                           std::to_string(programs_.size()) + " known");
    raw_ctrl_.clear();
    setpoints_.clear();
    loops_.clear();
    for (std::size_t a = 0; a < actuator_names_.size(); ++a) {
        const auto& channel = snapshot.channels[a];
        const std::string& name = actuator_names_[a];
        if (channel.has_raw) raw_ctrl_[name] = channel.raw;
        if (channel.has_setpoint) setpoints_[name] = channel.setpoint;
        if (channel.has_pid) loops_[name] = Loop{channel.gains, channel.integral, channel.previous_error, channel.has_previous};
    }
    program_ = snapshot.program;
    program_start_ = snapshot.program_start;
    cursor_ = snapshot.segment;
}

void ControllerPort::forget_programs_after(int program) {
    if (program_ > program) throw PhysicsError("can't forget the running program");
    programs_.resize(static_cast<std::size_t>(std::max(program + 1, 0)));
}

// ---------------------------------------------------------------- snapshot encoding
std::vector<std::string> ControllerSnapshot::column_names(const std::vector<std::string>& actuators) {
    static const char* fields[kChannelWidth] = {"has_raw", "raw", "has_setpoint", "setpoint", "has_pid", "kp",
                                                "ki", "kd", "integral_limit", "integral", "previous_error", "has_previous"};
    std::vector<std::string> names;
    for (const auto& actuator : actuators)
        for (const char* field : fields) names.push_back("control." + actuator + "." + field);
    for (const char* field : {"control.program", "control.program_start", "control.segment"}) names.push_back(field);
    return names;
}

void ControllerSnapshot::write(std::span<double> out) const {
    if (out.size() != width(channels.size())) throw PhysicsError("controller snapshot: wrong block width");
    std::size_t i = 0;
    for (const Channel& c : channels) {
        for (double value : {double(c.has_raw), c.raw, double(c.has_setpoint), c.setpoint, double(c.has_pid), c.gains.kp,
                             c.gains.ki, c.gains.kd, c.gains.integral_limit, c.integral, c.previous_error, double(c.has_previous)})
            out[i++] = value;
    }
    out[i++] = double(program);
    out[i++] = program_start;
    out[i++] = double(segment);
}

ControllerSnapshot ControllerSnapshot::read(std::span<const double> in, std::size_t actuators) {
    if (in.size() != width(actuators)) throw PhysicsError("controller snapshot: wrong block width");
    ControllerSnapshot snapshot;
    std::size_t i = 0;
    for (std::size_t a = 0; a < actuators; ++a, i += kChannelWidth) {
        Channel c;
        c.has_raw = in[i] != 0;
        c.raw = in[i + 1];
        c.has_setpoint = in[i + 2] != 0;
        c.setpoint = in[i + 3];
        c.has_pid = in[i + 4] != 0;
        c.gains = {in[i + 5], in[i + 6], in[i + 7], in[i + 8]};
        c.integral = in[i + 9];
        c.previous_error = in[i + 10];
        c.has_previous = in[i + 11] != 0;
        snapshot.channels.push_back(c);
    }
    snapshot.program = static_cast<int>(std::lround(in[i]));
    snapshot.program_start = in[i + 1];
    snapshot.segment = static_cast<std::size_t>(std::llround(in[i + 2]));
    return snapshot;
}

void ControllerPort::apply(const mjModel_* model, mjData_* data, double time, double dt) {
    std::map<std::string, double> targets = setpoints_;
    if (program_ >= 0)
        for (const auto& [name, value] : programs_[static_cast<std::size_t>(program_)]->sample(time - program_start_, cursor_))
            targets[name] = value;

    for (int a = 0; a < model->nu; ++a) {
        const std::string& name = actuator_names_[static_cast<std::size_t>(a)];
        double value = data->ctrl[a];
        if (auto raw = raw_ctrl_.find(name); raw != raw_ctrl_.end()) {
            value = raw->second;
        } else if (auto target = targets.find(name); target != targets.end()) {
            auto loop = loops_.find(name);
            const int qpos = actuator_qpos_[static_cast<std::size_t>(a)];
            if (loop != loops_.end() && qpos >= 0) {
                Loop& state = loop->second;
                const double error = target->second - data->qpos[qpos];
                state.integral = std::clamp(state.integral + error * dt, -state.gains.integral_limit, state.gains.integral_limit);
                const double derivative = state.has_previous ? (error - state.previous_error) / dt : 0.0;
                state.previous_error = error;
                state.has_previous = true;
                value = state.gains.kp * error + state.gains.ki * state.integral + state.gains.kd * derivative;
            } else {
                value = target->second;                       // position / velocity actuators take the setpoint
            }
        }
        if (model->actuator_ctrllimited[a])
            value = std::clamp(value, model->actuator_ctrlrange[2 * a], model->actuator_ctrlrange[2 * a + 1]);
        data->ctrl[a] = value;
    }

    for (const auto& spring : springs_) {
        if (spring.body < 0) continue;
        const Vec3 position(data->xipos + 3 * spring.body);
        mjtNum velocity[6];
        mj_objectVelocity(model, data, mjOBJ_BODY, spring.body, velocity, 0);     // [angular, linear], world frame
        const Vec3 linear(velocity[3], velocity[4], velocity[5]);
        Vec3 offset = position - spring.anchor;
        const double length = offset.norm();
        const Vec3 direction = length > 1e-12 ? Vec3(offset / length) : Vec3::Zero();
        const Vec3 force = -spring.stiffness * (length - spring.rest_length) * direction - spring.damping * linear;
        for (int i = 0; i < 3; ++i) data->xfrc_applied[6 * spring.body + i] += force[i];
    }
}

void ControllerPort::gather(const mjModel_* model, const mjData_* data, Tick tick, double time) {
    frame_.tick = tick;
    frame_.time = time;
    for (int s = 0; s < model->nsensor; ++s) {
        const char* name = mj_id2name(model, mjOBJ_SENSOR, s);
        const int address = model->sensor_adr[s];
        auto& values = frame_.sensors[name ? name : "sensor" + std::to_string(s)];
        values.assign(data->sensordata + address, data->sensordata + address + model->sensor_dim[s]);
    }
    frame_.ctrl.assign(data->ctrl, data->ctrl + model->nu);
    frame_.actuator_force.assign(data->actuator_force, data->actuator_force + model->nu);
    if (callback_) callback_(frame_);
}

}  // namespace devos::physics
