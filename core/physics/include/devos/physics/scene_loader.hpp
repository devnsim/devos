// ==============================================================================
// FILE: core/physics/include/devos/physics/scene_loader.hpp
// OVERVIEW: SceneLoader - reads a scene bundle (MJCF + convex collision meshes, e.g. CoACD parts exported by
//           the Part Design workbench) and routes it: articulated machines (robots, gantries, drones) to
//           MuJoCo, the room (floors, fixtures, loose bodies) to Jolt.
// PURPOSE:
//   1. Bundle = a folder with machine.xml (MuJoCo) and / or environment.xml (Jolt); a single .xml file is a
//      machine-only scene. Mesh assets are resolved by MuJoCo (meshdir / file paths relative to the XML).
//   2. environment.xml routing: bodies without joints (and worldbody geoms) -> static Jolt bodies; bodies with
//      a <freejoint/> -> dynamic Jolt bodies (mass from MJCF); any other joint -> warning, treated as static
//      (articulation belongs in machine.xml).
//   3. Machine proxies: every machine body with collision geoms gets a kinematic Jolt twin (same shapes) so
//      the room can touch the machine (see CoupledStepper).
//   4. Geoms become ShapeDesc: box, sphere, capsule, cylinder, plane, convex mesh (meshes are convex hulls -
//      decompose concave parts first, e.g. with CoACD); ellipsoids become sampled hulls; hfield / sdf skipped.
// CONTRACT:
//   - Requires: types.hpp; MuJoCo only via the forward-declared mjModel_ (headers stay MuJoCo-free).
//   - Exposes: SceneLoader::load(path) -> SceneBundle; ShapeDesc, EnvironmentBody, MachineProxy, SceneBundle,
//              SceneLoader::machine_proxies(model)
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
// ==============================================================================
#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "devos/physics/types.hpp"

struct mjModel_;

namespace devos::physics {

struct ShapeDesc {
    enum class Kind { Box, Sphere, Capsule, Cylinder, Plane, ConvexMesh };
    Kind kind = Kind::Box;
    Vec3 size = Vec3::Zero();          // MuJoCo geom_size: box half extents; sphere (r); capsule / cylinder (r, half length)
    Pose local;                        // relative to its body
    std::vector<Vec3> vertices;        // ConvexMesh: hull points in the geom frame
    double friction = 0.8;
    std::string name;
};

struct EnvironmentBody {
    std::string name;
    bool is_dynamic = false;
    double mass = 0;                   // dynamic bodies
    Pose pose;                         // world pose at load
    std::vector<ShapeDesc> shapes;
};

struct MachineProxy {
    int mujoco_body = -1;              // body id in the machine model
    std::string name;
    std::vector<ShapeDesc> shapes;
};

struct SceneBundle {
    std::filesystem::path source;                    // what was loaded (folder or file)
    std::filesystem::path machine_xml, environment_xml;
    std::shared_ptr<mjModel_> machine;               // owned MuJoCo model (mj_deleteModel on release); may be empty
    std::vector<MachineProxy> proxies;
    std::vector<EnvironmentBody> environment;
    std::vector<std::string> warnings;
};

class SceneLoader {
public:
    static SceneBundle load(const std::filesystem::path& path);
    static std::vector<MachineProxy> machine_proxies(const mjModel_* model, std::vector<std::string>& warnings);
};

}  // namespace devos::physics
