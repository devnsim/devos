// ==============================================================================
// FILE: core/physics/tests/test_physics.cpp
// OVERVIEW: Self-contained tests for libdevos_physics (CTest runs this executable; no test framework needed).
// PURPOSE:
//   1. WorldlineBuffer ring arithmetic, truncation, copies.
//   2. ControllerPort pieces: S-curve limits, trajectory sampling, G-code parsing / timing.
//   3. PhysicsWorld on the demo bundle: routing (MuJoCo / Jolt), stepping, gravity on the loose box,
//      setpoint tracking, coupling force when the paddle meets the room, determinism, reset, seek, branch.
//   4. Closed-loop control across seek / branch: PID integrators, commands given mid-run and a running G-code
//      program (segment cursor) come back from the worldline bit for bit.
// CONTRACT:
//   - Requires: libdevos_physics, DEVOS_PHYSICS_SCENES (compile definition: core/physics/scenes)
//   - Exposes: devos_physics_tests executable (exit code = number of failed checks)
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] Trajectory cursor sampling, ControllerSnapshot encoding, closed-loop seek / branch checks.
// ==============================================================================
#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

#include "devos/physics/physics_world.hpp"

using namespace devos::physics;

namespace {

int failures = 0;
int checks = 0;

void check(bool condition, const std::string& what) {
    ++checks;
    if (!condition) {
        ++failures;
        std::printf("  FAIL: %s\n", what.c_str());
    }
}

void test(const char* name, const std::function<void()>& body) {
    std::printf("%s\n", name);
    try {
        body();
    } catch (const std::exception& error) {
        ++failures;
        std::printf("  FAIL: threw %s\n", error.what());
    }
}

const std::string kDemo = std::string(DEVOS_PHYSICS_SCENES) + "/demo";

double column(const PhysicsWorld& world, Tick tick, const std::string& name) {
    return world.worldline().row(tick)[*world.worldline().layout().column(name)];
}

}  // namespace

int main() {
    test("worldline ring", [] {
        WorldlineLayout layout;
        layout.columns = {"time", "tick", "x"};
        layout.observation_width = 3;
        layout.restore_offset = 3;
        WorldlineBuffer line(layout, 4);
        for (Tick tick = 0; tick < 6; ++tick) {
            auto row = line.write_row(tick);
            row[0] = tick * 0.1;
            row[1] = double(tick);
            row[2] = double(tick * tick);
        }
        check(line.size() == 4 && line.oldest_tick() == 2 && line.newest_tick() == 5, "keeps the newest 4 rows");
        check(line.row(3)[2] == 9.0 && !line.has_tick(1), "finds rows by tick, drops the oldest");
        std::vector<double> copy;
        line.copy_range(3, 5, copy);
        check(copy.size() == 9 && copy[8] == 25.0, "copy_range in time order");
        line.truncate_after(3);
        check(line.newest_tick() == 3 && line.size() == 2, "truncate_after drops the future");
        line.write_row(4)[2] = 1.0;
        check(line.row(4)[2] == 1.0, "writes continue after truncation");
        bool threw = false;
        try { line.write_row(9); } catch (const PhysicsError&) { threw = true; }
        check(threw, "out-of-order writes are refused");
    });

    test("s-curve, trajectory, g-code", [] {
        const SCurve move = SCurve::plan(0.0, 1.0, 0.5, 2.0);
        check(std::abs(move.position(move.duration) - 1.0) < 1e-12 && move.position(0) == 0.0, "starts and ends on target");
        double peak = 0;
        for (int i = 0; i <= 1000; ++i) peak = std::max(peak, std::abs(move.velocity(move.duration * i / 1000.0)));
        check(peak <= 0.5 + 1e-9, "peak velocity within the limit");
        const GcodeProgram program = GcodeProgram::parse("G21 G90 (setup)\nG0 X10 Y0\nG1 X10 Y20 F600 ; slow\nG4 P500\nG91 G1 X-5\n");
        check(program.moves().size() == 4, "parses moves and dwells");
        check(program.moves()[3].target.at('X') == 5.0, "relative moves accumulate");
        const Trajectory path = program.to_trajectory({{'X', {"x_motor", 0.001}}, {'Y', {"y_motor", 0.001}}}, {});
        const auto end = path.sample(path.duration() + 1);
        check(std::abs(end.at("x_motor") - 0.005) < 1e-12 && std::abs(end.at("y_motor") - 0.020) < 1e-12, "ends at the last target (scaled)");
        check(path.duration() > 2.0, "feed 600 mm/min over 20 mm takes at least 2 s");
        std::size_t cursor = 0;
        bool same = true;
        for (int i = 0; i <= 400; ++i) {
            const double t = path.duration() * 1.05 * i / 400.0;
            same &= path.sample(t, cursor) == path.sample(t);
        }
        check(same && cursor == path.segments().size() - 1, "cursor sampling matches a full search");
        cursor = 3;
        check(path.sample(0.01, cursor) == path.sample(0.01) && cursor == 0, "the cursor walks back after a jump");
        bool threw = false;
        try { GcodeProgram::parse("G1 X"); } catch (const PhysicsError&) { threw = true; }
        check(threw, "malformed words are errors");
    });

    test("demo scene loads and routes", [] {
        PhysicsWorld world;
        world.load_scene(kDemo);
        const SceneSummary summary = world.summary();
        check(summary.mujoco_bodies == 2 && summary.actuators == 2 && summary.sensors == 5, "machine in MuJoCo");
        check(summary.proxies == 2, "machine bodies mirrored as Jolt proxies");
        check(summary.static_bodies == 3 && summary.dynamic_bodies == 1, "room in Jolt (world, fixture, ramp + loose box)");
        check(world.tick() == 0 && world.worldline().size() == 1, "tick 0 recorded");
    });

    test("stepping, gravity, setpoints, coupling", [] {
        WorldConfig config;
        config.rate_hz = 1000;
        PhysicsWorld world(config);
        world.load_scene(kDemo);
        const double box_start = world.pose("loose_box")->position.z();
        world.controller().set_setpoint("shoulder_motor", 0.6);    // sweep the paddle through the loose box
        world.step(1.5);
        check(world.tick() == 1500, "1.5 s = 1500 ticks at 1 kHz");
        check(std::abs(column(world, 1500, "time") - 1.5) < 1e-9, "worldline time column");
        check(std::abs(world.controller().last_frame().sensors.at("shoulder_angle")[0] - 0.6) < 0.05, "position actuator tracks the setpoint");
        check(std::abs(world.pose("loose_box")->position.z() - box_start) < 0.02, "the box rests on the floor (Jolt gravity + floor)");
        double largest_force = 0;
        for (Tick tick = 1; tick <= 1500; ++tick)
            for (const char* axis : {"x", "y", "z"})
                largest_force = std::max(largest_force, std::abs(column(world, tick, std::string("contact.forearm.f") + axis)));
        check(largest_force > 0.0, "the room pushed back on the forearm (coupling wrench)");
        const auto moved = world.pose("loose_box")->position - Vec3(0.62, 0.25, 0.05);
        check(moved.head<2>().norm() > 0.01, "the paddle moved the loose box");
    });

    test("determinism, reset, seek, branch", [] {
        auto run = [] {
            auto world = std::make_unique<PhysicsWorld>();
            world->load_scene(kDemo);
            world->controller().set_setpoint("shoulder_motor", -0.9);
            world->controller().set_setpoint("elbow_motor", 0.4);
            world->step_ticks(800);
            return world;
        };
        auto first = run(), second = run();
        const auto a = first->worldline().row(800), b = second->worldline().row(800);
        bool identical = true;
        for (std::size_t i = 0; i < a.size(); ++i) identical &= a[i] == b[i];
        check(identical, "two runs are bit-identical");

        const std::vector<double> before(first->worldline().row(300).begin(), first->worldline().row(300).end());
        first->seek(300);
        check(first->tick() == 300 && first->worldline().newest_tick() == 300, "seek moves the clock and drops the future");
        check(std::abs(first->time() - 0.3) < 1e-12, "seek restores MuJoCo time");
        const auto restored = first->worldline().row(300);
        check(std::equal(before.begin(), before.end(), restored.begin()), "the seeked row is unchanged");
        first->step_ticks(500);
        const auto replayed = first->worldline().row(800);
        bool replay_exact = true;
        for (std::size_t i = 0; i < replayed.size(); ++i) replay_exact &= replayed[i] == b[i];
        check(replay_exact, "seek + re-run reproduces the original bit for bit (Jolt keyframes)");
        first->seek(310);
        check(first->tick() == 310, "seek again to an earlier tick");

        auto branch = first->branch(305);
        check(branch->tick() == 305 && std::abs(branch->time() - 0.305) < 1e-12, "branch starts at the requested tick");
        branch->controller().set_setpoint("shoulder_motor", 0.0);
        branch->step_ticks(50);
        check(first->tick() == 310, "the branch is independent");

        first->reset();
        check(first->tick() == 0 && first->time() == 0.0 && first->worldline().size() == 1, "reset back to tick 0");
    });

    test("controller snapshot encoding", [] {
        ControllerSnapshot snapshot;
        snapshot.channels.resize(2);
        snapshot.channels[0] = {true, 0.25, true, -1.5, true, {2, 3, 0.5, 7}, 0.125, -0.0625, true};
        snapshot.channels[1].has_setpoint = true;
        snapshot.channels[1].setpoint = 0.75;
        snapshot.program = 1;
        snapshot.program_start = 0.4;
        snapshot.segment = 5;
        std::vector<double> block(ControllerSnapshot::width(2));
        snapshot.write(block);
        check(ControllerSnapshot::read(block, 2) == snapshot, "write / read round trip");
        check(ControllerSnapshot::column_names({"a", "b"}).size() == block.size(), "one column name per value");
    });

    test("closed-loop control across seek and branch", [] {
        const Trajectory program = GcodeProgram::parse("G1 X20 F6000\nG4 P100\nG1 X-10\n")
                                       .to_trajectory({{'X', {"elbow_motor", 0.0174533}}}, {});
        auto start = [] {
            auto world = std::make_unique<PhysicsWorld>();
            world->load_scene(kDemo);
            world->controller().set_pid("shoulder_motor", {2.0, 8.0, 0.05});
            world->controller().set_setpoint("shoulder_motor", 0.8);
            world->step_ticks(400);
            return world;
        };
        auto commands_at_400 = [&](PhysicsWorld& world) {        // given between tick 400 and 401
            world.controller().set_setpoint("shoulder_motor", -0.3);
            world.controller().run_trajectory(program, world.time());
        };
        auto world = start();
        commands_at_400(*world);
        world->step_ticks(600);
        const std::vector<double> reference(world->worldline().row(1000).begin(), world->worldline().row(1000).end());
        const std::size_t control = *world->worldline().layout().column("restore.control.program");
        const std::size_t shoulder = *world->worldline().layout().column("restore.control.shoulder_motor.integral");
        check(world->worldline().row(400)[control] == 0.0, "the program given after tick 400 belongs to row 400");
        check(world->worldline().row(399)[control] == -1.0, "row 399 had no program");
        check(world->worldline().row(900)[control + 2] == 2.0, "row 900 is on the program's third segment");
        check(world->worldline().row(700)[shoulder] != 0.0, "the PID integrator is recorded");

        auto rows_match = [&](const PhysicsWorld& other) {
            const auto row = other.worldline().row(1000);
            return std::equal(row.begin(), row.end(), reference.begin());
        };
        world->seek(250);       // back before the mid-run commands: the old setpoint and integrator return
        check(world->controller().snapshot().channels[0].setpoint == 0.8, "seek restores the setpoint of that tick");
        world->step_ticks(150);
        commands_at_400(*world);
        world->step_ticks(600);
        check(rows_match(*world), "seek before the commands, give them again: bit-identical");

        world->seek(700);       // inside the program, mid-loop: nothing given again
        check(world->controller().snapshot().segment == 0 && world->controller().snapshot().program == 0,
              "seek restores the program and its cursor");
        world->step_ticks(300);
        check(rows_match(*world), "seek inside the program and re-run: bit-identical (PID + cursor restored)");

        auto branch = world->branch(650);
        branch->step_ticks(350);
        check(rows_match(*branch), "a branch continues the closed loop bit for bit");

        auto fresh = start();   // the old behaviour (integrators restart) would differ
        commands_at_400(*fresh);
        fresh->step_ticks(300);
        fresh->controller().reset_loop_state();
        fresh->step_ticks(300);
        check(!rows_match(*fresh), "restarting the integrators changes the run (the snapshot matters)");
    });

    std::printf("%d checks, %d failed\n", checks, failures);
    return failures;
}
