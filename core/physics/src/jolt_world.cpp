// ==============================================================================
// FILE: core/physics/src/jolt_world.cpp
// OVERVIEW: JoltWorld implementation: Jolt setup, layers, shapes from ShapeDesc, bodies, contact capture.
// PURPOSE:
//   1. Process-wide Jolt registration, reference counted across worlds (thread-safe).
//   2. ShapeDesc -> Jolt shapes. MuJoCo capsules / cylinders run along local Z, Jolt's along Y: a rotation of
//      +90 deg about X maps one onto the other. Planes become a large, 1 m thick box under the plane.
//   3. A ContactListener that keeps the contacts involving proxies (single-threaded job system, so the
//      callbacks arrive on the stepping thread in a deterministic order).
// CONTRACT:
//   - Requires: jolt_world.hpp, Jolt Physics (Jolt/Jolt.h first)
//   - Exposes: JoltWorld member definitions
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
// ==============================================================================
#include <Jolt/Jolt.h>

#include <Jolt/Core/Factory.h>
#include <Jolt/Core/JobSystemSingleThreaded.h>
#include <Jolt/Core/TempAllocator.h>
#include <Jolt/Physics/Body/BodyCreationSettings.h>
#include <Jolt/Physics/Collision/ContactListener.h>
#include <Jolt/Physics/Collision/Shape/BoxShape.h>
#include <Jolt/Physics/Collision/Shape/CapsuleShape.h>
#include <Jolt/Physics/Collision/Shape/ConvexHullShape.h>
#include <Jolt/Physics/Collision/Shape/CylinderShape.h>
#include <Jolt/Physics/Collision/Shape/RotatedTranslatedShape.h>
#include <Jolt/Physics/Collision/Shape/SphereShape.h>
#include <Jolt/Physics/Collision/Shape/StaticCompoundShape.h>
#include <Jolt/Physics/PhysicsSettings.h>
#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/Physics/StateRecorderImpl.h>
#include <Jolt/RegisterTypes.h>

#include "jolt_world.hpp"

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <mutex>

namespace devos::physics::detail {
namespace {

// ---------------------------------------------------------------- process-wide setup
std::mutex registration_mutex;
int registration_count = 0;

void trace(const char* format, ...) {
    va_list arguments;
    va_start(arguments, format);
    std::fprintf(stderr, "[jolt] ");
    std::vfprintf(stderr, format, arguments);
    std::fprintf(stderr, "\n");
    va_end(arguments);
}

void acquire_jolt() {
    std::lock_guard lock(registration_mutex);
    if (registration_count++ == 0) {
        JPH::RegisterDefaultAllocator();
        JPH::Trace = trace;
        JPH::Factory::sInstance = new JPH::Factory();
        JPH::RegisterTypes();
    }
}

void release_jolt() {
    std::lock_guard lock(registration_mutex);
    if (--registration_count == 0) {
        JPH::UnregisterTypes();
        delete JPH::Factory::sInstance;
        JPH::Factory::sInstance = nullptr;
    }
}

// ---------------------------------------------------------------- layers
namespace layers {
constexpr JPH::ObjectLayer kStatic = 0, kDynamic = 1, kProxy = 2, kCount = 3;
}
namespace broad {
constexpr JPH::BroadPhaseLayer kStatic(0), kMoving(1);
constexpr JPH::uint kCount = 2;
}

class BroadPhaseLayers final : public JPH::BroadPhaseLayerInterface {
public:
    JPH::uint GetNumBroadPhaseLayers() const override { return broad::kCount; }
    JPH::BroadPhaseLayer GetBroadPhaseLayer(JPH::ObjectLayer layer) const override {
        return layer == layers::kStatic ? broad::kStatic : broad::kMoving;
    }
#if defined(JPH_EXTERNAL_PROFILE) || defined(JPH_PROFILE_ENABLED)
    const char* GetBroadPhaseLayerName(JPH::BroadPhaseLayer layer) const override {
        return layer == broad::kStatic ? "static" : "moving";
    }
#endif
};

class ObjectVsBroadPhase final : public JPH::ObjectVsBroadPhaseLayerFilter {
public:
    bool ShouldCollide(JPH::ObjectLayer layer, JPH::BroadPhaseLayer broad_layer) const override {
        return layer == layers::kStatic ? broad_layer == broad::kMoving : true;
    }
};

class ObjectPairs final : public JPH::ObjectLayerPairFilter {
public:
    bool ShouldCollide(JPH::ObjectLayer a, JPH::ObjectLayer b) const override {
        if (a == layers::kStatic) return b != layers::kStatic;
        if (a == layers::kProxy) return b != layers::kProxy;
        return true;
    }
};

// ---------------------------------------------------------------- conversions
JPH::Vec3 to_jolt(const Vec3& v) { return {float(v.x()), float(v.y()), float(v.z())}; }
JPH::Quat to_jolt(const Quat& q) { return JPH::Quat(float(q.x()), float(q.y()), float(q.z()), float(q.w())).Normalized(); }
Vec3 from_jolt(JPH::Vec3Arg v) { return {v.GetX(), v.GetY(), v.GetZ()}; }
Quat from_jolt(JPH::QuatArg q) { return Quat(q.GetW(), q.GetX(), q.GetY(), q.GetZ()); }

enum class Kind : std::uint32_t { Static = 1, Dynamic = 2, Proxy = 3 };
JPH::uint64 user_data(Kind kind, std::size_t index) { return (JPH::uint64(kind) << 32) | JPH::uint64(index); }
Kind kind_of(JPH::uint64 data) { return Kind(std::uint32_t(data >> 32)); }
std::size_t index_of(JPH::uint64 data) { return std::size_t(data & 0xffffffffu); }

JPH::ShapeRefC create(const JPH::ShapeSettings& settings, const std::string& what) {
    JPH::ShapeSettings::ShapeResult result = settings.Create();
    if (result.HasError()) throw PhysicsError("Jolt shape for '" + what + "': " + std::string(result.GetError().c_str()));
    return result.Get();
}

JPH::ShapeRefC make_shape(const ShapeDesc& shape) {
    const JPH::Quat y_to_z = JPH::Quat::sRotation(JPH::Vec3::sAxisX(), 0.5f * JPH::JPH_PI);
    const float a = float(shape.size.x()), b = float(shape.size.y()), c = float(shape.size.z());
    switch (shape.kind) {
        case ShapeDesc::Kind::Box: {
            const float radius = std::min(0.05f, 0.5f * std::min({a, b, c}));
            return create(JPH::BoxShapeSettings(JPH::Vec3(a, b, c), radius), shape.name);
        }
        case ShapeDesc::Kind::Sphere:
            return create(JPH::SphereShapeSettings(a), shape.name);
        case ShapeDesc::Kind::Capsule:
            return create(JPH::RotatedTranslatedShapeSettings(JPH::Vec3::sZero(), y_to_z, new JPH::CapsuleShapeSettings(b, a)), shape.name);
        case ShapeDesc::Kind::Cylinder: {
            const float radius = std::min(0.05f, 0.5f * std::min(a, b));
            return create(JPH::RotatedTranslatedShapeSettings(JPH::Vec3::sZero(), y_to_z, new JPH::CylinderShapeSettings(b, a, radius)),
                          shape.name);
        }
        case ShapeDesc::Kind::Plane: {
            const float half_x = a > 0 ? a : 50.0f, half_y = b > 0 ? b : 50.0f;
            return create(JPH::RotatedTranslatedShapeSettings(JPH::Vec3(0, 0, -0.5f), JPH::Quat::sIdentity(),
                                                             new JPH::BoxShapeSettings(JPH::Vec3(half_x, half_y, 0.5f))), shape.name);
        }
        case ShapeDesc::Kind::ConvexMesh: {
            JPH::Array<JPH::Vec3> points;
            points.reserve(shape.vertices.size());
            for (const Vec3& vertex : shape.vertices) points.push_back(to_jolt(vertex));
            if (points.size() < 4) throw PhysicsError("mesh '" + shape.name + "' needs at least 4 vertices");
            return create(JPH::ConvexHullShapeSettings(points, 0.002f), shape.name);
        }
    }
    throw PhysicsError("unknown shape kind");
}

JPH::ShapeRefC make_body_shape(const std::vector<ShapeDesc>& shapes, const std::string& body) {
    if (shapes.empty()) throw PhysicsError("body '" + body + "' has no collision shapes");
    if (shapes.size() == 1) {
        const ShapeDesc& only = shapes.front();
        JPH::ShapeRefC inner = make_shape(only);
        if (only.local.position.isZero() && only.local.orientation.isApprox(Quat::Identity())) return inner;
        return create(JPH::RotatedTranslatedShapeSettings(to_jolt(only.local.position), to_jolt(only.local.orientation), inner), body);
    }
    JPH::StaticCompoundShapeSettings compound;
    for (const ShapeDesc& shape : shapes) compound.AddShape(to_jolt(shape.local.position), to_jolt(shape.local.orientation), make_shape(shape));
    return create(compound, body);
}

double average_friction(const std::vector<ShapeDesc>& shapes) {
    double total = 0;
    for (const auto& shape : shapes) total += shape.friction;
    return shapes.empty() ? 0.8 : total / double(shapes.size());
}

}  // namespace

// ---------------------------------------------------------------- implementation state
struct JoltWorld::Impl final : public JPH::ContactListener {
    BroadPhaseLayers broad_phase_layers;
    ObjectVsBroadPhase object_vs_broad_phase;
    ObjectPairs object_pairs;
    std::unique_ptr<JPH::TempAllocatorImpl> temp_allocator;
    std::unique_ptr<JPH::JobSystemSingleThreaded> job_system;
    std::unique_ptr<JPH::PhysicsSystem> system;

    std::vector<JPH::BodyID> statics, dynamics, proxies;
    std::vector<std::string> dynamic_names;
    std::vector<double> dynamic_masses;
    std::vector<char> touched_non_proxy;
    std::vector<ProxyContact> contacts;

    void record(const JPH::Body& first, const JPH::Body& second, const JPH::ContactManifold& manifold) {
        const Kind kind_first = kind_of(first.GetUserData()), kind_second = kind_of(second.GetUserData());
        for (const auto& [body, other_kind] : {std::pair{&first, kind_second}, std::pair{&second, kind_first}})
            if (kind_of(body->GetUserData()) == Kind::Dynamic && other_kind != Kind::Proxy)
                touched_non_proxy[index_of(body->GetUserData())] = 1;
        const bool proxy_is_first = kind_first == Kind::Proxy;
        if (!proxy_is_first && kind_second != Kind::Proxy) return;
        if (manifold.mPenetrationDepth <= 0.0f || manifold.mRelativeContactPointsOn1.empty()) return;   // speculative
        const JPH::Body& proxy = proxy_is_first ? first : second;
        const JPH::Body& other = proxy_is_first ? second : first;
        JPH::Vec3 point = JPH::Vec3::sZero();
        const JPH::uint count = manifold.mRelativeContactPointsOn1.size();
        for (JPH::uint i = 0; i < count; ++i)
            point += JPH::Vec3(proxy_is_first ? manifold.GetWorldSpaceContactPointOn1(i) : manifold.GetWorldSpaceContactPointOn2(i));
        point /= float(count);
        ProxyContact contact;
        contact.proxy = int(index_of(proxy.GetUserData()));
        contact.point = from_jolt(point);
        // mWorldSpaceNormal moves body 2 out of body 1.
        contact.push = from_jolt(proxy_is_first ? -manifold.mWorldSpaceNormal : manifold.mWorldSpaceNormal);
        contact.depth = manifold.mPenetrationDepth;
        contact.proxy_velocity = from_jolt(proxy.GetPointVelocity(JPH::RVec3(point)));
        contact.other_velocity = other.IsStatic() ? Vec3::Zero() : from_jolt(other.GetPointVelocity(JPH::RVec3(point)));
        contact.dynamic_index = kind_of(other.GetUserData()) == Kind::Dynamic ? int(index_of(other.GetUserData())) : -1;
        contacts.push_back(contact);
    }

    void OnContactAdded(const JPH::Body& first, const JPH::Body& second, const JPH::ContactManifold& manifold,
                        JPH::ContactSettings&) override { record(first, second, manifold); }
    void OnContactPersisted(const JPH::Body& first, const JPH::Body& second, const JPH::ContactManifold& manifold,
                            JPH::ContactSettings&) override { record(first, second, manifold); }
};

JoltWorld::JoltWorld(const Vec3& gravity, unsigned max_bodies) : impl_(std::make_unique<Impl>()) {
    acquire_jolt();
    impl_->temp_allocator = std::make_unique<JPH::TempAllocatorImpl>(16 * 1024 * 1024);
    impl_->job_system = std::make_unique<JPH::JobSystemSingleThreaded>(JPH::cMaxPhysicsJobs);
    impl_->system = std::make_unique<JPH::PhysicsSystem>();
    impl_->system->Init(max_bodies, 0, max_bodies, max_bodies * 2, impl_->broad_phase_layers, impl_->object_vs_broad_phase,
                        impl_->object_pairs);
    impl_->system->SetGravity(to_jolt(gravity));
    impl_->system->SetContactListener(impl_.get());
}

JoltWorld::~JoltWorld() {
    JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    for (auto* list : {&impl_->statics, &impl_->dynamics, &impl_->proxies}) {
        if (list->empty()) continue;
        bodies.RemoveBodies(list->data(), int(list->size()));
        bodies.DestroyBodies(list->data(), int(list->size()));
    }
    impl_->system.reset();
    impl_->job_system.reset();
    impl_->temp_allocator.reset();
    impl_.reset();
    release_jolt();
}

void JoltWorld::add_environment(const EnvironmentBody& body) {
    JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    const bool dynamic = body.is_dynamic;
    JPH::BodyCreationSettings settings(make_body_shape(body.shapes, body.name), JPH::RVec3(to_jolt(body.pose.position)),
                                       to_jolt(body.pose.orientation), dynamic ? JPH::EMotionType::Dynamic : JPH::EMotionType::Static,
                                       dynamic ? layers::kDynamic : layers::kStatic);
    settings.mFriction = float(average_friction(body.shapes));
    if (dynamic) {
        settings.mOverrideMassProperties = JPH::EOverrideMassProperties::CalculateInertia;
        settings.mMassPropertiesOverride.mMass = float(body.mass);
        settings.mUserData = user_data(Kind::Dynamic, impl_->dynamics.size());
    } else {
        settings.mUserData = user_data(Kind::Static, impl_->statics.size());
    }
    const JPH::BodyID id = bodies.CreateAndAddBody(settings, dynamic ? JPH::EActivation::Activate : JPH::EActivation::DontActivate);
    if (id.IsInvalid()) throw PhysicsError("Jolt is out of bodies (max_bodies)");
    if (dynamic) {
        impl_->dynamics.push_back(id);
        impl_->dynamic_names.push_back(body.name);
        impl_->dynamic_masses.push_back(body.mass);
        impl_->touched_non_proxy.push_back(0);
    } else {
        impl_->statics.push_back(id);
    }
}

void JoltWorld::add_proxy(const MachineProxy& proxy, const Pose& pose) {
    JPH::BodyCreationSettings settings(make_body_shape(proxy.shapes, proxy.name), JPH::RVec3(to_jolt(pose.position)),
                                       to_jolt(pose.orientation), JPH::EMotionType::Kinematic, layers::kProxy);
    settings.mCollideKinematicVsNonDynamic = true;           // so the room's static fixtures report contacts
    settings.mFriction = float(average_friction(proxy.shapes));
    settings.mUserData = user_data(Kind::Proxy, impl_->proxies.size());
    const JPH::BodyID id = impl_->system->GetBodyInterface().CreateAndAddBody(settings, JPH::EActivation::Activate);
    if (id.IsInvalid()) throw PhysicsError("Jolt is out of bodies (max_bodies)");
    impl_->proxies.push_back(id);
}

void JoltWorld::finish_loading() { impl_->system->OptimizeBroadPhase(); }

void JoltWorld::move_proxy(std::size_t index, const Pose& target, double dt) {
    impl_->system->GetBodyInterface().MoveKinematic(impl_->proxies.at(index), JPH::RVec3(to_jolt(target.position)),
                                                    to_jolt(target.orientation), float(dt));
}

void JoltWorld::place_proxy(std::size_t index, const Pose& pose) {
    JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    const JPH::BodyID id = impl_->proxies.at(index);
    bodies.SetPositionAndRotation(id, JPH::RVec3(to_jolt(pose.position)), to_jolt(pose.orientation), JPH::EActivation::Activate);
    bodies.SetLinearAndAngularVelocity(id, JPH::Vec3::sZero(), JPH::Vec3::sZero());
}

void JoltWorld::step(double dt) {
    impl_->contacts.clear();
    std::fill(impl_->touched_non_proxy.begin(), impl_->touched_non_proxy.end(), 0);
    const JPH::EPhysicsUpdateError error = impl_->system->Update(float(dt), 1, impl_->temp_allocator.get(), impl_->job_system.get());
    if (error != JPH::EPhysicsUpdateError::None) trace("update reported error flags %u (raise max_bodies / contact limits)", unsigned(error));
}

std::size_t JoltWorld::proxy_count() const { return impl_->proxies.size(); }
std::size_t JoltWorld::dynamic_count() const { return impl_->dynamics.size(); }
std::size_t JoltWorld::static_count() const { return impl_->statics.size(); }
const std::string& JoltWorld::dynamic_name(std::size_t index) const { return impl_->dynamic_names.at(index); }
double JoltWorld::dynamic_mass(std::size_t index) const { return impl_->dynamic_masses.at(index); }
bool JoltWorld::dynamic_touched_non_proxy(std::size_t index) const { return impl_->touched_non_proxy.at(index) != 0; }
const std::vector<ProxyContact>& JoltWorld::contacts() const { return impl_->contacts; }
Vec3 JoltWorld::gravity() const { return from_jolt(impl_->system->GetGravity()); }

std::string JoltWorld::save_state() const {
    JPH::StateRecorderImpl recorder;
    impl_->system->SaveState(recorder);
    return recorder.GetData();
}

void JoltWorld::restore_state(const std::string& snapshot) {
    JPH::StateRecorderImpl recorder;
    recorder.WriteBytes(snapshot.data(), snapshot.size());
    recorder.Rewind();
    if (!impl_->system->RestoreState(recorder)) throw PhysicsError("Jolt snapshot doesn't match this world");
}

BodyState JoltWorld::dynamic_state(std::size_t index) const {
    const JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    const JPH::BodyID id = impl_->dynamics.at(index);
    return {from_jolt(JPH::Vec3(bodies.GetPosition(id))), from_jolt(bodies.GetRotation(id)), from_jolt(bodies.GetLinearVelocity(id)),
            from_jolt(bodies.GetAngularVelocity(id))};
}

void JoltWorld::set_dynamic_state(std::size_t index, const BodyState& state) {
    JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    const JPH::BodyID id = impl_->dynamics.at(index);
    bodies.SetPositionAndRotation(id, JPH::RVec3(to_jolt(state.position)), to_jolt(state.orientation), JPH::EActivation::Activate);
    bodies.SetLinearAndAngularVelocity(id, to_jolt(state.linear_velocity), to_jolt(state.angular_velocity));
}

double JoltWorld::dynamic_kinetic_energy(std::size_t index) const {
    const JPH::BodyInterface& bodies = impl_->system->GetBodyInterface();
    const JPH::BodyID id = impl_->dynamics.at(index);
    const Vec3 linear = from_jolt(bodies.GetLinearVelocity(id));
    const Vec3 angular = from_jolt(bodies.GetAngularVelocity(id));
    const JPH::Mat44 inverse = bodies.GetInverseInertia(id);
    Eigen::Matrix3d inverse_inertia;
    for (int row = 0; row < 3; ++row)
        for (int column = 0; column < 3; ++column) inverse_inertia(row, column) = inverse(JPH::uint(row), JPH::uint(column));
    double rotational = 0;
    if (std::abs(inverse_inertia.determinant()) > 1e-18) rotational = 0.5 * angular.dot(inverse_inertia.inverse() * angular);
    return 0.5 * dynamic_mass(index) * linear.squaredNorm() + rotational;
}

}  // namespace devos::physics::detail
