// ==============================================================================
// FILE: core/physics/src/scene_loader.cpp
// OVERVIEW: Bundle reading and geom -> ShapeDesc conversion for SceneLoader (routing rules in the header).
// PURPOSE:
//   1. load_mjcf(): mj_loadXML with readable errors; the machine model is kept, the environment model is only
//      read (forward kinematics for world poses) and released.
//   2. shapes_of_body(): every collision geom of a body (contype or conaffinity set) as a ShapeDesc.
// CONTRACT:
//   - Requires: scene_loader.hpp, MuJoCo (mujoco/mujoco.h)
//   - Exposes: SceneLoader::load(), SceneLoader::machine_proxies()
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
// ==============================================================================
#include "devos/physics/scene_loader.hpp"

#include <array>
#include <cmath>

#include <mujoco/mujoco.h>

namespace devos::physics {
namespace {

std::shared_ptr<mjModel> load_mjcf(const std::filesystem::path& path) {
    if (!std::filesystem::exists(path)) throw PhysicsError("scene file not found: " + path.string());
    std::array<char, 1024> error{};
    mjModel* model = mj_loadXML(path.string().c_str(), nullptr, error.data(), static_cast<int>(error.size()));
    if (model == nullptr) throw PhysicsError("MJCF " + path.filename().string() + ": " + error.data());
    return {model, [](mjModel* doomed) { mj_deleteModel(doomed); }};
}

std::string name_of(const mjModel* model, mjtObj type, int id, const char* fallback) {
    const char* name = mj_id2name(model, type, id);
    return name ? name : std::string(fallback) + std::to_string(id);
}

bool collides(const mjModel* model, int geom) {
    return model->geom_contype[geom] != 0 || model->geom_conaffinity[geom] != 0;
}

std::vector<ShapeDesc> shapes_of_body(const mjModel* model, int body, std::vector<std::string>& warnings) {
    std::vector<ShapeDesc> shapes;
    for (int g = model->body_geomadr[body]; g >= 0 && g < model->body_geomadr[body] + model->body_geomnum[body]; ++g) {
        if (!collides(model, g)) continue;
        ShapeDesc shape;
        shape.name = name_of(model, mjOBJ_GEOM, g, "geom");
        shape.size = Vec3(model->geom_size[3 * g], model->geom_size[3 * g + 1], model->geom_size[3 * g + 2]);
        shape.local.position = Vec3(model->geom_pos[3 * g], model->geom_pos[3 * g + 1], model->geom_pos[3 * g + 2]);
        shape.local.orientation = mujoco_quat(model->geom_quat + 4 * g);
        shape.friction = model->geom_friction[3 * g];
        switch (model->geom_type[g]) {
            case mjGEOM_BOX: shape.kind = ShapeDesc::Kind::Box; break;
            case mjGEOM_SPHERE: shape.kind = ShapeDesc::Kind::Sphere; break;
            case mjGEOM_CAPSULE: shape.kind = ShapeDesc::Kind::Capsule; break;
            case mjGEOM_CYLINDER: shape.kind = ShapeDesc::Kind::Cylinder; break;
            case mjGEOM_PLANE: shape.kind = ShapeDesc::Kind::Plane; break;
            case mjGEOM_ELLIPSOID: {
                shape.kind = ShapeDesc::Kind::ConvexMesh;          // sampled hull: 12 x 6 points on the ellipsoid
                for (int i = 0; i < 12; ++i)
                    for (int j = 0; j <= 6; ++j) {
                        const double azimuth = 2 * M_PI * i / 12, polar = M_PI * j / 6;
                        shape.vertices.emplace_back(shape.size.x() * std::sin(polar) * std::cos(azimuth),
                                                    shape.size.y() * std::sin(polar) * std::sin(azimuth),
                                                    shape.size.z() * std::cos(polar));
                    }
                break;
            }
            case mjGEOM_MESH: {
                shape.kind = ShapeDesc::Kind::ConvexMesh;
                const int mesh = model->geom_dataid[g];
                const int first = model->mesh_vertadr[mesh], count = model->mesh_vertnum[mesh];
                shape.vertices.reserve(static_cast<std::size_t>(count));
                for (int v = first; v < first + count; ++v)
                    shape.vertices.emplace_back(model->mesh_vert[3 * v], model->mesh_vert[3 * v + 1], model->mesh_vert[3 * v + 2]);
                break;
            }
            default:
                warnings.push_back("geom '" + shape.name + "': type not supported in Jolt (height field / SDF), skipped");
                continue;
        }
        shapes.push_back(std::move(shape));
    }
    return shapes;
}

bool has_free_joint(const mjModel* model, int body) {
    for (int j = model->body_jntadr[body]; j >= 0 && j < model->body_jntadr[body] + model->body_jntnum[body]; ++j)
        if (model->jnt_type[j] == mjJNT_FREE) return true;
    return false;
}

std::vector<EnvironmentBody> environment_bodies(const mjModel* model, std::vector<std::string>& warnings) {
    mjData* data = mj_makeData(model);
    mj_kinematics(model, data);                                   // world poses of every body
    std::vector<EnvironmentBody> bodies;
    for (int b = 0; b < model->nbody; ++b) {
        EnvironmentBody body;
        body.name = b == 0 ? "world" : name_of(model, mjOBJ_BODY, b, "body");
        body.shapes = shapes_of_body(model, b, warnings);
        if (body.shapes.empty()) continue;
        const bool is_free = has_free_joint(model, b);
        if (!is_free && model->body_jntnum[b] > 0)
            warnings.push_back("environment body '" + body.name + "' has joints other than a free joint: treated as "
                               "static (articulated machines belong in machine.xml)");
        if (is_free && model->body_parentid[b] != 0)
            warnings.push_back("environment body '" + body.name + "' is free but nested: its world pose at load is used");
        body.is_dynamic = is_free;
        body.mass = model->body_mass[b];
        body.pose.position = Vec3(data->xpos[3 * b], data->xpos[3 * b + 1], data->xpos[3 * b + 2]);
        body.pose.orientation = mujoco_quat(data->xquat + 4 * b);
        if (body.is_dynamic && body.mass <= 0) {
            warnings.push_back("environment body '" + body.name + "' is free but has no mass: given 1 kg");
            body.mass = 1.0;
        }
        bodies.push_back(std::move(body));
    }
    mj_deleteData(data);
    return bodies;
}

}  // namespace

std::vector<MachineProxy> SceneLoader::machine_proxies(const mjModel_* model, std::vector<std::string>& warnings) {
    std::vector<MachineProxy> proxies;
    for (int b = 1; b < model->nbody; ++b) {                       // the world body stays MuJoCo-only
        MachineProxy proxy{b, name_of(model, mjOBJ_BODY, b, "body"), shapes_of_body(model, b, warnings)};
        if (!proxy.shapes.empty()) proxies.push_back(std::move(proxy));
    }
    return proxies;
}

SceneBundle SceneLoader::load(const std::filesystem::path& path) {
    SceneBundle bundle;
    bundle.source = path;
    if (std::filesystem::is_directory(path)) {
        if (std::filesystem::exists(path / "machine.xml")) bundle.machine_xml = path / "machine.xml";
        if (std::filesystem::exists(path / "environment.xml")) bundle.environment_xml = path / "environment.xml";
        if (bundle.machine_xml.empty() && bundle.environment_xml.empty())
            throw PhysicsError("scene folder " + path.string() + " has neither machine.xml nor environment.xml");
    } else {
        bundle.machine_xml = path;
    }
    if (!bundle.machine_xml.empty()) {
        bundle.machine = load_mjcf(bundle.machine_xml);
        bundle.proxies = machine_proxies(bundle.machine.get(), bundle.warnings);
    }
    if (!bundle.environment_xml.empty()) {
        auto environment = load_mjcf(bundle.environment_xml);
        bundle.environment = environment_bodies(environment.get(), bundle.warnings);
    }
    return bundle;
}

}  // namespace devos::physics
