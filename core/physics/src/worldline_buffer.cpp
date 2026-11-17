// ==============================================================================
// FILE: core/physics/src/worldline_buffer.cpp
// OVERVIEW: Ring-buffer arithmetic of WorldlineBuffer (see the header for the idea).
// PURPOSE:
//   1. Slot bookkeeping for a contiguous capacity x width std::vector<double>.
// CONTRACT:
//   - Requires: devos/physics/worldline_buffer.hpp
//   - Exposes: the WorldlineBuffer / WorldlineLayout member definitions
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] update_row(tick).
// ==============================================================================
#include "devos/physics/worldline_buffer.hpp"

#include <algorithm>
#include <utility>

namespace devos::physics {

std::optional<std::size_t> WorldlineLayout::column(const std::string& name) const {
    auto found = std::find(columns.begin(), columns.end(), name);
    if (found == columns.end()) return std::nullopt;
    return static_cast<std::size_t>(found - columns.begin());
}

WorldlineBuffer::WorldlineBuffer(WorldlineLayout layout, std::size_t capacity_rows)
    : layout_(std::move(layout)), capacity_(std::max<std::size_t>(capacity_rows, 1)) {
    if (layout_.width() < 2) throw PhysicsError("worldline layout needs at least time and tick columns");
    storage_.assign(capacity_ * layout_.width(), 0.0);
}

std::span<double> WorldlineBuffer::write_row(Tick tick) {
    if (count_ > 0 && tick != newest_tick() + 1)
        throw PhysicsError("worldline rows must be written in tick order (got " + std::to_string(tick) +
                           " after " + std::to_string(newest_tick()) + ")");
    std::size_t slot;
    if (count_ == 0) {
        head_ = 0;
        oldest_ = tick;
        count_ = 1;
        slot = 0;
    } else if (count_ < capacity_) {
        slot = (head_ + count_) % capacity_;
        ++count_;
    } else {                                    // full: the oldest row becomes the newest
        slot = head_;
        head_ = (head_ + 1) % capacity_;
        ++oldest_;
    }
    return {storage_.data() + slot * width(), width()};
}

std::optional<std::size_t> WorldlineBuffer::slot_of(Tick tick) const {
    if (count_ == 0 || tick < oldest_ || tick > newest_tick()) return std::nullopt;
    return (head_ + static_cast<std::size_t>(tick - oldest_)) % capacity_;
}

bool WorldlineBuffer::has_tick(Tick tick) const { return slot_of(tick).has_value(); }

std::span<const double> WorldlineBuffer::row(Tick tick) const {
    auto slot = slot_of(tick);
    if (!slot)
        throw PhysicsError("tick " + std::to_string(tick) + " is outside the worldline window [" +
                           std::to_string(oldest_) + ", " + std::to_string(count_ ? newest_tick() : 0) + "]");
    return {storage_.data() + *slot * width(), width()};
}

std::span<double> WorldlineBuffer::update_row(Tick tick) {
    const auto row = std::as_const(*this).row(tick);
    return {const_cast<double*>(row.data()), row.size()};
}

Tick WorldlineBuffer::oldest_tick() const { return oldest_; }
Tick WorldlineBuffer::newest_tick() const { return count_ ? oldest_ + count_ - 1 : oldest_; }

void WorldlineBuffer::truncate_after(Tick tick) {
    if (count_ == 0 || tick >= newest_tick()) return;
    count_ = tick < oldest_ ? 0 : static_cast<std::size_t>(tick - oldest_) + 1;
}

void WorldlineBuffer::clear() {
    count_ = 0;
    head_ = 0;
    oldest_ = 0;
}

void WorldlineBuffer::copy_range(Tick first, Tick last, std::vector<double>& out) const {
    if (first > last || !has_tick(first) || !has_tick(last)) throw PhysicsError("copy_range outside the worldline window");
    out.resize(static_cast<std::size_t>(last - first + 1) * width());
    for (Tick tick = first; tick <= last; ++tick) {
        auto source = row(tick);
        std::copy(source.begin(), source.end(), out.begin() + static_cast<std::ptrdiff_t>((tick - first) * width()));
    }
}

}  // namespace devos::physics
