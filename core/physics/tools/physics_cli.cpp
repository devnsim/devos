// ==============================================================================
// FILE: core/physics/tools/physics_cli.cpp
// OVERVIEW: devos_physics_cli - headless runner for the physics engine: load a scene bundle, drive it
//           (setpoints or a G-code file), step it, print a report, optionally write the worldline as CSV and
//           check determinism and seek / replay.
// PURPOSE:
//   1. Smoke test and benchmark without Python or Qt: real-time factor, contacts, energies, final poses.
//   2. --verify: two fresh runs must end bit-identical (determinism); seeking to the middle and re-running,
//      and a branch from the middle, must both reproduce the original end state bit for bit.
// USAGE:
//   devos_physics_cli <scene folder | machine.xml> [--seconds S] [--rate HZ] [--set actuator=value ...]
//                     [--pid actuator=kp,ki,kd ...] [--gcode file --axis X=actuator[:scale] ...]
//                     [--csv out.csv] [--verify]
// CONTRACT:
//   - Requires: libdevos_physics (devos/physics/physics_world.hpp). No Python, no Qt.
//   - Exposes: the devos_physics_cli executable (exit code 0 = ok, 1 = error, 2 = a --verify check failed)
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] The branch check no longer re-drives the branch: it inherits the controller (programs,
//                  setpoints, PID state) from the worldline, so the check now covers controller restore too.
//                  --pid actuator=kp,ki,kd closes a PID loop on an actuator's joint.
// ==============================================================================
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "devos/physics/physics_world.hpp"

using namespace devos::physics;

namespace {

struct Options {
    std::string scene;
    double seconds = 2.0;
    double rate = 1000.0;
    std::map<std::string, double> setpoints;
    std::map<std::string, PidGains> pids;
    std::string gcode_file;
    std::map<char, AxisBinding> axes;
    std::string csv;
    bool verify = false;
};

[[noreturn]] void usage(const std::string& problem) {
    std::cerr << "devos_physics_cli: " << problem << "\n"
              << "usage: devos_physics_cli <scene> [--seconds S] [--rate HZ] [--set actuator=value]...\n"
              << "                         [--pid actuator=kp,ki,kd]... [--gcode file --axis X=actuator[:scale]...]\n"
              << "                         [--csv out.csv] [--verify]\n";
    std::exit(1);
}

Options parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) usage(arg + " needs a value");
            return argv[++i];
        };
        if (arg == "--seconds") options.seconds = std::stod(next());
        else if (arg == "--rate") options.rate = std::stod(next());
        else if (arg == "--csv") options.csv = next();
        else if (arg == "--gcode") options.gcode_file = next();
        else if (arg == "--verify") options.verify = true;
        else if (arg == "--set") {
            const std::string spec = next();
            const auto equals = spec.find('=');
            if (equals == std::string::npos) usage("--set needs actuator=value");
            options.setpoints[spec.substr(0, equals)] = std::stod(spec.substr(equals + 1));
        } else if (arg == "--pid") {
            const std::string spec = next();                  // actuator=kp,ki,kd
            const auto equals = spec.find('=');
            if (equals == std::string::npos) usage("--pid needs actuator=kp,ki,kd");
            std::stringstream gains(spec.substr(equals + 1));
            PidGains pid;
            char comma1 = 0, comma2 = 0;
            if (!(gains >> pid.kp >> comma1 >> pid.ki >> comma2 >> pid.kd) || comma1 != ',' || comma2 != ',')
                usage("--pid needs actuator=kp,ki,kd");
            options.pids[spec.substr(0, equals)] = pid;
        } else if (arg == "--axis") {
            const std::string spec = next();                  // X=actuator[:scale]
            if (spec.size() < 3 || spec[1] != '=') usage("--axis needs X=actuator[:scale]");
            AxisBinding binding;
            const std::string rest = spec.substr(2);
            const auto colon = rest.find(':');
            binding.actuator = rest.substr(0, colon);
            if (colon != std::string::npos) binding.scale = std::stod(rest.substr(colon + 1));
            options.axes[char(std::toupper(spec[0]))] = binding;
        } else if (!arg.empty() && arg[0] == '-') usage("unknown option " + arg);
        else options.scene = arg;
    }
    if (options.scene.empty()) usage("no scene given");
    return options;
}

void drive(PhysicsWorld& world, const Options& options) {
    for (const auto& [actuator, value] : options.setpoints) world.controller().set_setpoint(actuator, value);
    for (const auto& [actuator, gains] : options.pids) world.controller().set_pid(actuator, gains);
    if (!options.gcode_file.empty()) {
        std::ifstream file(options.gcode_file);
        if (!file) throw PhysicsError("can't read " + options.gcode_file);
        std::stringstream text;
        text << file.rdbuf();
        if (options.axes.empty()) throw PhysicsError("--gcode needs at least one --axis binding");
        const Trajectory trajectory = GcodeProgram::parse(text.str()).to_trajectory(options.axes, {});
        std::cout << "G-code: " << trajectory.segments().size() << " segments, " << trajectory.duration() << " s\n";
        world.controller().run_trajectory(trajectory, world.time());
    }
}

std::unique_ptr<PhysicsWorld> run(const Options& options, std::size_t ticks) {
    WorldConfig config;
    config.rate_hz = options.rate;
    config.worldline_capacity = std::max<std::size_t>(ticks + 1, 1);
    auto world = std::make_unique<PhysicsWorld>(config);
    world->load_scene(options.scene);
    drive(*world, options);
    world->step_ticks(ticks);
    return world;
}

bool rows_equal(std::span<const double> a, std::span<const double> b, std::size_t width) {
    for (std::size_t i = 0; i < width; ++i)
        if (a[i] != b[i]) return false;
    return true;
}

void write_csv(const PhysicsWorld& world, const std::string& path) {
    std::ofstream out(path);
    const WorldlineBuffer& line = world.worldline();
    const auto& layout = line.layout();
    for (std::size_t c = 0; c < layout.observation_width; ++c) out << (c ? "," : "") << layout.columns[c];
    out << "\n";
    out.precision(10);
    for (Tick tick = line.oldest_tick(); tick <= line.newest_tick(); ++tick) {
        const auto row = line.row(tick);
        for (std::size_t c = 0; c < layout.observation_width; ++c) out << (c ? "," : "") << row[c];
        out << "\n";
    }
    std::cout << "Worldline: " << line.size() << " rows x " << layout.observation_width << " columns -> " << path << "\n";
}

}  // namespace

int main(int argc, char** argv) {
    const Options options = parse(argc, argv);
    try {
        const auto ticks = std::size_t(std::llround(options.seconds * options.rate));
        const auto started = std::chrono::steady_clock::now();
        auto world = run(options, ticks);
        const double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();

        const SceneSummary summary = world->summary();
        std::cout << "Scene: " << summary.source << "\n"
                  << "  MuJoCo: " << summary.mujoco_bodies << " bodies, " << summary.mujoco_joints << " joints, "
                  << summary.actuators << " actuators, " << summary.sensors << " sensors\n"
                  << "  Jolt:   " << summary.proxies << " machine proxies, " << summary.static_bodies << " static, "
                  << summary.dynamic_bodies << " dynamic\n"
                  << "  Worldline row: " << summary.worldline_width << " doubles\n";
        for (const auto& warning : world->warnings()) std::cout << "  warning: " << warning << "\n";
        std::printf("Ran %zu ticks (%.3f s simulated at %.0f Hz) in %.3f s wall: %.1fx real time\n", ticks, world->time(),
                    options.rate, wall, wall > 0 ? world->time() / wall : 0.0);

        const auto& layout = world->worldline().layout();
        const auto last = world->worldline().row(world->tick());
        std::printf("Energy: kinetic %.6f J, potential %.6f J; proxy contacts last tick: %zu\n",
                    last[*layout.column("kinetic_energy")], last[*layout.column("potential_energy")], world->contact_count());
        for (const auto& name : world->body_names()) {
            const auto pose = world->pose(name);
            std::printf("  %-14s at (%+.4f, %+.4f, %+.4f)\n", name.c_str(), pose->position.x(), pose->position.y(), pose->position.z());
        }
        for (const auto& [name, values] : world->controller().last_frame().sensors) {
            std::printf("  sensor %-16s", name.c_str());
            for (double value : values) std::printf(" %+.4f", value);
            std::printf("\n");
        }
        if (!options.csv.empty()) write_csv(*world, options.csv);

        if (options.verify) {
            int failures = 0;
            auto again = run(options, ticks);
            const bool deterministic = rows_equal(world->worldline().row(ticks), again->worldline().row(ticks), layout.width());
            std::cout << "verify: two runs bit-identical: " << (deterministic ? "yes" : "NO") << "\n";
            failures += !deterministic;

            const Tick middle = ticks / 2;
            std::vector<double> reference(world->worldline().row(ticks).begin(), world->worldline().row(ticks).end());
            world->seek(middle);                    // restores the controller too: no inputs are given again
            world->step_ticks(ticks - middle);
            const auto replay = world->worldline().row(ticks);
            double worst = 0;
            for (std::size_t c = 0; c < layout.width(); ++c) worst = std::max(worst, std::abs(replay[c] - reference[c]));
            const bool exact = rows_equal(replay, reference, layout.width());
            std::printf("verify: seek to tick %llu and replay: bit-identical: %s (largest difference %.3g)\n",
                        static_cast<unsigned long long>(middle), exact ? "yes" : "NO", worst);
            failures += !exact;

            auto branch = world->branch(middle);
            branch->step_ticks(ticks - middle);
            const bool branch_exact = rows_equal(branch->worldline().row(ticks), reference, layout.width());
            std::cout << "verify: branch at tick " << middle << " run to the end matches the original: "
                      << (branch_exact ? "yes" : "NO") << "\n";
            failures += !branch_exact;
            return failures ? 2 : 0;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "devos_physics_cli: " << error.what() << "\n";
        return 1;
    }
}
