// ==============================================================================
// FILE: core/physics/include/devos/physics/controller_port.hpp
// OVERVIEW: ControllerPort - where outside logic drives the simulation. Setpoints (direct, PID,
//           S-curve moves, G-code toolpaths) become MuJoCo actuator controls (mjData->ctrl); virtual
//           spring-dampers (indoor drone mesh / swarm) become body forces; sensor readings come back as a
//           clean SensorFrame every tick.
// PURPOSE:
//   1. Actuator setpoints: set_ctrl() (raw), set_setpoint() + set_pid() (closed loop on the actuator's
//      joint position), with ctrlrange clamping.
//   2. Trajectories: SCurve (jerk-limited minimum-jerk move), GcodeProgram (G0 / G1 / G4 / G20 / G21 /
//      G90 / G91 with an axis map) turned into a Trajectory sampled every tick.
//   3. Springs: add_spring(body, anchor, stiffness, damping, rest_length) - force on the body's centre of mass.
//   4. Sensors: SensorFrame {tick, time, values per named sensor, ctrl, actuator forces}.
//   5. ControllerSnapshot: everything the port carries from one tick to the next (commands, PID gains and
//      integrators, the running program and its segment cursor) as a fixed-width block of doubles, stored in
//      every worldline row so seek / branch restore closed-loop control bit for bit.
// CONTRACT:
//   - Requires: types.hpp; MuJoCo only through the forward-declared mjModel_ / mjData_ (no MuJoCo headers
//               here), C++20 standard library.
//   - Exposes: ControllerPort, ControllerSnapshot, SCurve, Trajectory, GcodeProgram, AxisBinding, SensorFrame,
//              PidGains;
//              ControllerPort::bind(model) / apply(model, data, time) / gather(model, data, tick) are called
//              by the CoupledStepper every tick; snapshot() / restore() by PhysicsWorld.
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] ControllerSnapshot (commands, PID state, program + segment cursor) so seek and branch no
//                  longer restart PID integrators; trajectories are sampled through a segment cursor; programs
//                  are kept in a list so a snapshot can name the one that was running.
// ==============================================================================
#pragma once

#include <functional>
#include <map>
#include <memory>
#include <span>
#include <string>
#include <vector>

#include "devos/physics/types.hpp"

struct mjModel_;
struct mjData_;

namespace devos::physics {

// Jerk-limited point-to-point move (minimum-jerk quintic): smooth start and stop, peak velocity and
// acceleration kept within the limits by choosing the duration.
struct SCurve {
    double start = 0, end = 0, duration = 0;
    static SCurve plan(double start, double end, double max_velocity, double max_acceleration);
    double position(double t) const;
    double velocity(double t) const;
};

// Setpoints per actuator over time: consecutive S-curve segments (with dwells).
class Trajectory {
public:
    struct Segment {
        double start_time = 0, duration = 0;
        std::map<std::string, SCurve> axes;     // actuator name -> move during this segment
    };
    void add_segment(Segment segment);
    double duration() const;
    std::map<std::string, double> sample(double t) const;   // setpoint per actuator at time t
    // The same, starting the search at `cursor` (the segment index) and leaving it on the segment for t:
    // O(1) per tick when t only moves forward.
    std::map<std::string, double> sample(double t, std::size_t& cursor) const;
    bool empty() const { return segments_.empty(); }
    const std::vector<Segment>& segments() const { return segments_; }

private:
    std::vector<Segment> segments_;
    std::vector<std::map<std::string, double>> held_;    // per segment: end values of earlier segments
    std::map<std::string, double> final_;
};

// G-code axis letter -> actuator, with a scale (machine units per G-code unit, e.g. 0.001 for mm -> m).
struct AxisBinding {
    std::string actuator;
    double scale = 0.001;
    double offset = 0.0;
};

class GcodeProgram {
public:
    // Parses G0 / G1 (X Y Z A B C, F feed), G4 P (dwell, ms) / S (s), G20 / G21 (inch / mm), G90 / G91.
    static GcodeProgram parse(const std::string& text);
    // Time-parameterised setpoints. Feed F is in G-code units (mm) per minute; G0 uses rapid_feed. Each
    // move is an S-curve along the path, limited by the feed and max_acceleration (mm / s^2); `start` is
    // the axes' position before the program (mm).
    Trajectory to_trajectory(const std::map<char, AxisBinding>& axes, const std::map<char, double>& start,
                             double rapid_feed = 3000.0, double max_acceleration = 2000.0) const;

    struct Move {
        bool is_rapid = false;
        double dwell_s = 0;                     // > 0: a pause instead of a move
        double feed = 0;                        // units / min (0 = keep previous)
        std::map<char, double> target;          // axis -> position (absolute, mm)
    };
    const std::vector<Move>& moves() const { return moves_; }

private:
    std::vector<Move> moves_;
};

struct PidGains {
    double kp = 0, ki = 0, kd = 0;
    double integral_limit = 1e9;
    bool operator==(const PidGains&) const = default;
};

// Everything the ControllerPort carries from one tick to the next, flattened into a fixed-width block of
// doubles (the worldline's restore block): restoring it brings closed-loop control back exactly.
struct ControllerSnapshot {
    struct Channel {                          // one per actuator, in model order
        bool has_raw = false;
        double raw = 0;                       // set_ctrl value
        bool has_setpoint = false;
        double setpoint = 0;
        bool has_pid = false;
        PidGains gains;
        double integral = 0, previous_error = 0;
        bool has_previous = false;
        bool operator==(const Channel&) const = default;
    };
    std::vector<Channel> channels;
    int program = -1;                         // index into ControllerPort::programs() (-1 = none running)
    double program_start = 0;                 // simulation time the program started
    std::size_t segment = 0;                  // cursor: the program segment being sampled

    static constexpr std::size_t kChannelWidth = 12;
    static constexpr std::size_t kProgramWidth = 3;
    static std::size_t width(std::size_t actuators) { return actuators * kChannelWidth + kProgramWidth; }
    static std::vector<std::string> column_names(const std::vector<std::string>& actuators);
    void write(std::span<double> out) const;                   // out.size() == width(channels.size())
    static ControllerSnapshot read(std::span<const double> in, std::size_t actuators);
    bool operator==(const ControllerSnapshot&) const = default;
};

struct SensorFrame {
    Tick tick = 0;
    double time = 0;
    std::map<std::string, std::vector<double>> sensors;   // sensor name -> its values
    std::vector<double> ctrl;                             // controls applied this tick
    std::vector<double> actuator_force;                   // resulting actuator forces
};

class ControllerPort {
public:
    // --- inputs (any thread-free caller between steps) ---
    void set_ctrl(const std::string& actuator, double value);
    void set_setpoint(const std::string& actuator, double target);
    void set_pid(const std::string& actuator, PidGains gains);
    void clear_pid(const std::string& actuator);
    void run_trajectory(Trajectory trajectory, double start_time);
    void stop_trajectory();
    bool trajectory_active(double time) const;
    int add_spring(const std::string& body, const Vec3& anchor, double stiffness, double damping, double rest_length = 0.0);
    void move_spring_anchor(int spring, const Vec3& anchor);
    void remove_springs();
    void on_sensor_frame(std::function<void(const SensorFrame&)> callback) { callback_ = std::move(callback); }

    // --- called by the stepper ---
    void bind(const mjModel_* model);                      // resolve names; resets loop state
    void apply(const mjModel_* model, mjData_* data, double time, double dt);   // writes ctrl (+ spring xfrc)
    void gather(const mjModel_* model, const mjData_* data, Tick tick, double time);
    void reset_loop_state();                               // PID integrators and the program cursor
    ControllerSnapshot snapshot() const;                   // after bind(): one channel per actuator
    void restore(const ControllerSnapshot& snapshot);       // commands, gains, loop state, program, cursor
    void forget_programs_after(int program);               // drop programs newer than `program` (after a seek)

    const SensorFrame& last_frame() const { return frame_; }
    std::vector<std::string> actuator_names() const { return actuator_names_; }
    // Every program passed to run_trajectory(), oldest first (a snapshot names one by index).
    const std::vector<std::shared_ptr<const Trajectory>>& programs() const { return programs_; }

private:
    struct Loop { PidGains gains; double integral = 0, previous_error = 0; bool has_previous = false; };
    struct Spring { int body = -1; std::string body_name; Vec3 anchor; double stiffness, damping, rest_length; };

    int actuator_index(const std::string& name) const;

    std::vector<std::string> actuator_names_;
    std::vector<int> actuator_qpos_;           // qpos address of the actuator's joint (-1 = not a joint)
    std::vector<int> actuator_dof_;
    std::map<std::string, double> raw_ctrl_;
    std::map<std::string, double> setpoints_;
    std::map<std::string, Loop> loops_;
    std::vector<std::shared_ptr<const Trajectory>> programs_;
    int program_ = -1;                         // running program (index into programs_), -1 = none
    double program_start_ = 0;
    std::size_t cursor_ = 0;                   // segment of the running program
    std::vector<Spring> springs_;
    SensorFrame frame_;
    std::function<void(const SensorFrame&)> callback_;
};

}  // namespace devos::physics
