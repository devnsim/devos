// ==============================================================================
// FILE: core/physics/python/bindings.cpp
// OVERVIEW: devos_physics Python module - a thin pybind11 adapter over libdevos_physics. No logic lives here:
//           every call forwards to the C++ API; the worldline is exposed zero-copy.
// PURPOSE:
//   1. PhysicsWorld (load_scene, step, step_ticks, reset, seek, branch, tick, time, pose, body_names, summary,
//      warnings, controller).
//   2. Worldline: worldline_ring() = a read-only NumPy view of the whole ring (capacity x width, no copy;
//      rows start at worldline_head()), worldline_rows(first, last) = a time-ordered copy, column names.
//   3. ControllerPort: setpoints, raw ctrl, PID, G-code programs, springs, the latest sensor frame, the
//      controller snapshot (commands + PID state per actuator, running program and cursor) as a dict.
// CONTRACT:
//   - Requires: pybind11, NumPy at runtime, libdevos_physics. Nothing from DevOS's app/ (strict boundary).
//   - Exposes: Python module devos_physics: PhysicsWorld, Controller (via world.controller), PhysicsError
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] Controller.snapshot() (read-only view of ControllerSnapshot).
// ==============================================================================
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl/filesystem.h>

#include "devos/physics/physics_world.hpp"

namespace py = pybind11;
using namespace devos::physics;

namespace {

py::tuple pose_tuple(const Pose& pose) {
    const auto& p = pose.position;
    const auto& q = pose.orientation;
    return py::make_tuple(py::make_tuple(p.x(), p.y(), p.z()), py::make_tuple(q.w(), q.x(), q.y(), q.z()));
}

py::array worldline_ring(py::object owner) {
    const WorldlineBuffer& line = owner.cast<const PhysicsWorld&>().worldline();
    const auto width = static_cast<py::ssize_t>(line.width());
    py::array view(py::dtype::of<double>(), {static_cast<py::ssize_t>(line.capacity()), width},
                   {width * static_cast<py::ssize_t>(sizeof(double)), static_cast<py::ssize_t>(sizeof(double))},
                   line.data(), owner);                        // `owner` keeps the world alive while viewed
    py::detail::array_proxy(view.ptr())->flags &= ~py::detail::npy_api::NPY_ARRAY_WRITEABLE_;
    return view;
}

py::array_t<double> worldline_rows(const PhysicsWorld& world, Tick first, Tick last) {
    std::vector<double> rows;
    world.worldline().copy_range(first, last, rows);
    const auto width = static_cast<py::ssize_t>(world.worldline().width());
    py::array_t<double> out({static_cast<py::ssize_t>(rows.size()) / width, width});
    std::copy(rows.begin(), rows.end(), out.mutable_data());
    return out;
}

}  // namespace

PYBIND11_MODULE(devos_physics, m) {
    m.doc() = "DevOS physics engine (MuJoCo + Jolt), thin adapter over libdevos_physics";
    py::register_exception<PhysicsError>(m, "PhysicsError", PyExc_RuntimeError);

    py::class_<PidGains>(m, "PidGains")
        .def(py::init([](double kp, double ki, double kd, double integral_limit) { return PidGains{kp, ki, kd, integral_limit}; }),
             py::arg("kp") = 0.0, py::arg("ki") = 0.0, py::arg("kd") = 0.0, py::arg("integral_limit") = 1e9);

    py::class_<ControllerPort>(m, "Controller")
        .def("set_ctrl", &ControllerPort::set_ctrl, py::arg("actuator"), py::arg("value"))
        .def("set_setpoint", &ControllerPort::set_setpoint, py::arg("actuator"), py::arg("target"))
        .def("set_pid", &ControllerPort::set_pid, py::arg("actuator"), py::arg("gains"))
        .def("clear_pid", &ControllerPort::clear_pid, py::arg("actuator"))
        .def("run_gcode",
             [](ControllerPort& port, const std::string& text, const std::map<std::string, std::pair<std::string, double>>& axes,
                double start_time, double rapid_feed, double max_acceleration) {
                 std::map<char, AxisBinding> bindings;
                 for (const auto& [letter, binding] : axes) {
                     if (letter.size() != 1) throw PhysicsError("G-code axes are single letters, got '" + letter + "'");
                     bindings[char(std::toupper(letter[0]))] = AxisBinding{binding.first, binding.second, 0.0};
                 }
                 const Trajectory trajectory = GcodeProgram::parse(text).to_trajectory(bindings, {}, rapid_feed, max_acceleration);
                 port.run_trajectory(trajectory, start_time);
                 return trajectory.duration();
             },
             py::arg("text"), py::arg("axes"), py::arg("start_time"), py::arg("rapid_feed") = 3000.0, py::arg("max_acceleration") = 2000.0,
             "Run a G-code program; axes = {'X': ('actuator', scale)}. Returns its duration in seconds.")
        .def("stop_trajectory", &ControllerPort::stop_trajectory)
        .def("add_spring",
             [](ControllerPort& port, const std::string& body, std::array<double, 3> anchor, double stiffness, double damping, double rest) {
                 return port.add_spring(body, Vec3(anchor[0], anchor[1], anchor[2]), stiffness, damping, rest);
             },
             py::arg("body"), py::arg("anchor"), py::arg("stiffness"), py::arg("damping"), py::arg("rest_length") = 0.0)
        .def("move_spring_anchor",
             [](ControllerPort& port, int spring, std::array<double, 3> anchor) {
                 port.move_spring_anchor(spring, Vec3(anchor[0], anchor[1], anchor[2]));
             })
        .def("remove_springs", &ControllerPort::remove_springs)
        .def_property_readonly("actuator_names", &ControllerPort::actuator_names)
        .def("sensor_frame", [](const ControllerPort& port) {
            const SensorFrame& frame = port.last_frame();
            py::dict out;
            out["tick"] = frame.tick;
            out["time"] = frame.time;
            out["sensors"] = frame.sensors;
            out["ctrl"] = frame.ctrl;
            out["actuator_force"] = frame.actuator_force;
            return out;
        })
        .def("snapshot", [](const ControllerPort& port) {
            const ControllerSnapshot snapshot = port.snapshot();
            const auto names = port.actuator_names();
            py::dict channels;
            for (std::size_t a = 0; a < snapshot.channels.size(); ++a) {
                const auto& c = snapshot.channels[a];
                py::dict channel;
                channel["raw"] = c.has_raw ? py::cast(c.raw) : py::none();
                channel["setpoint"] = c.has_setpoint ? py::cast(c.setpoint) : py::none();
                channel["pid"] = c.has_pid ? py::cast(c.gains) : py::none();
                channel["integral"] = c.integral;
                channel["previous_error"] = c.has_previous ? py::cast(c.previous_error) : py::none();
                channels[py::str(names[a])] = channel;
            }
            py::dict out;
            out["actuators"] = channels;
            out["program"] = snapshot.program;
            out["program_start"] = snapshot.program_start;
            out["segment"] = snapshot.segment;
            return out;
        });

    py::class_<PhysicsWorld>(m, "PhysicsWorld")
        .def(py::init([](double rate_hz, std::size_t worldline_capacity, double contact_stiffness, double contact_damping) {
                 WorldConfig config;
                 config.rate_hz = rate_hz;
                 config.worldline_capacity = worldline_capacity;
                 config.coupling.contact_stiffness = contact_stiffness;
                 config.coupling.contact_damping = contact_damping;
                 return std::make_unique<PhysicsWorld>(config);
             }),
             py::arg("rate_hz") = 1000.0, py::arg("worldline_capacity") = 60000, py::arg("contact_stiffness") = 2.0e4,
             py::arg("contact_damping") = 2.0e2)
        .def("load_scene", &PhysicsWorld::load_scene, py::arg("path"), py::call_guard<py::gil_scoped_release>())
        .def("step", &PhysicsWorld::step, py::arg("dt"), py::call_guard<py::gil_scoped_release>())
        .def("step_ticks", &PhysicsWorld::step_ticks, py::arg("ticks"), py::call_guard<py::gil_scoped_release>())
        .def("reset", &PhysicsWorld::reset)
        .def("seek", &PhysicsWorld::seek, py::arg("tick"))
        .def("branch", &PhysicsWorld::branch, py::arg("tick"))
        .def_property_readonly("tick", &PhysicsWorld::tick)
        .def_property_readonly("time", &PhysicsWorld::time)
        .def_property_readonly("substep", &PhysicsWorld::substep)
        .def_property_readonly("is_loaded", &PhysicsWorld::is_loaded)
        .def_property_readonly("contact_count", &PhysicsWorld::contact_count)
        .def("pose", [](const PhysicsWorld& world, const std::string& body) -> py::object {
            const auto pose = world.pose(body);
            return pose ? py::object(pose_tuple(*pose)) : py::none();
        }, py::arg("body"), "((x, y, z), (qw, qx, qy, qz)) or None")
        .def("body_names", &PhysicsWorld::body_names)
        .def_property_readonly("warnings", &PhysicsWorld::warnings)
        .def_property_readonly("controller", &PhysicsWorld::controller, py::return_value_policy::reference_internal)
        .def("summary", [](const PhysicsWorld& world) {
            const SceneSummary s = world.summary();
            py::dict out;
            out["source"] = s.source;
            out["mujoco_bodies"] = s.mujoco_bodies;
            out["mujoco_joints"] = s.mujoco_joints;
            out["actuators"] = s.actuators;
            out["sensors"] = s.sensors;
            out["proxies"] = s.proxies;
            out["static_bodies"] = s.static_bodies;
            out["dynamic_bodies"] = s.dynamic_bodies;
            out["worldline_width"] = s.worldline_width;
            return out;
        })
        .def_property_readonly("worldline_columns", [](const PhysicsWorld& world) { return world.worldline().layout().columns; })
        .def_property_readonly("worldline_observation_width", [](const PhysicsWorld& world) { return world.worldline().layout().observation_width; })
        .def_property_readonly("worldline_oldest", [](const PhysicsWorld& world) { return world.worldline().oldest_tick(); })
        .def_property_readonly("worldline_newest", [](const PhysicsWorld& world) { return world.worldline().newest_tick(); })
        .def_property_readonly("worldline_head", [](const PhysicsWorld& world) { return world.worldline().head_slot(); })
        .def("worldline_ring", &worldline_ring, "Read-only NumPy view of the ring (capacity x width), no copy")
        .def("worldline_rows", &worldline_rows, py::arg("first"), py::arg("last"), "Rows first..last (inclusive), time-ordered copy");
}
