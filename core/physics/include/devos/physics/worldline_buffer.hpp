// ==============================================================================
// FILE: core/physics/include/devos/physics/worldline_buffer.hpp
// OVERVIEW: WorldlineBuffer - the 4D spacetime memory arena. Every simulation tick is one flat row of
//           doubles in a contiguous ring buffer: what happened (for streaming / plots) and the exact
//           solver state (for seeking back in time and branching).
// PURPOSE:
//   1. WorldlineLayout: names the columns of a row - time, tick, body poses, joint torques, coupling
//      forces, energies (the "observation" block) and the solver restore block.
//   2. WorldlineBuffer: fixed-capacity ring of rows. write_row(tick) hands out the row to fill (no
//      allocation per tick); row(tick) finds any tick still in the window; data() is the raw storage
//      for zero-copy views (Python / NumPy, DevOS Graph and Tensor blocks).
//   3. truncate_after(tick) drops the future when the world seeks back, so the timeline stays linear.
// CONTRACT:
//   - Requires: C++20 standard library only.
//   - Exposes: WorldlineLayout, WorldlineBuffer(layout, capacity_rows) with write_row(), row(), update_row(), has_tick(),
//              oldest_tick(), newest_tick(), size(), capacity(), width(), data(), slot_of(), truncate_after(),
//              clear(), copy_range()
// NOTES:
//   - [2026-11-16] Created with the physics engine groundwork.
//   - [2026-11-17] update_row(tick): a writable row already in the window (PhysicsWorld refreshes the newest
//                  row's controller block when stepping resumes).
// ==============================================================================
#pragma once

#include <cstddef>
#include <optional>
#include <span>
#include <string>
#include <vector>

#include "devos/physics/types.hpp"

namespace devos::physics {

struct WorldlineLayout {
    std::vector<std::string> columns;        // one name per double in a row
    std::size_t observation_width = 0;       // columns [0, observation_width) are for viewing
    std::size_t restore_offset = 0;          // columns [restore_offset, width) restore the solvers

    std::size_t width() const { return columns.size(); }
    std::optional<std::size_t> column(const std::string& name) const;
    // Fixed leading columns of every layout.
    static constexpr std::size_t kTimeColumn = 0;
    static constexpr std::size_t kTickColumn = 1;
};

class WorldlineBuffer {
public:
    WorldlineBuffer() = default;
    WorldlineBuffer(WorldlineLayout layout, std::size_t capacity_rows);

    // The row for `tick`, overwriting the oldest row when full. Ticks must increase by one (or restart
    // after clear() / truncate_after()). Column 0 / 1 (time / tick) are filled by the caller.
    std::span<double> write_row(Tick tick);

    std::span<const double> row(Tick tick) const;            // throws PhysicsError if not in the window
    std::span<double> update_row(Tick tick);                 // the same row, writable
    bool has_tick(Tick tick) const;
    std::optional<std::size_t> slot_of(Tick tick) const;     // ring slot index (row position in data())

    Tick oldest_tick() const;
    Tick newest_tick() const;
    std::size_t size() const { return count_; }
    std::size_t capacity() const { return capacity_; }
    std::size_t width() const { return layout_.width(); }
    const WorldlineLayout& layout() const { return layout_; }

    const double* data() const { return storage_.data(); }   // capacity() x width(), row-major ring
    std::size_t head_slot() const { return head_; }          // slot of oldest_tick()

    void truncate_after(Tick tick);                          // keep ticks <= tick
    void clear();
    // Rows [first, last] (inclusive) copied in time order into `out` (resized), e.g. for a branch.
    void copy_range(Tick first, Tick last, std::vector<double>& out) const;

private:
    WorldlineLayout layout_;
    std::vector<double> storage_;
    std::size_t capacity_ = 0;
    std::size_t head_ = 0;          // slot of the oldest row
    std::size_t count_ = 0;
    Tick oldest_ = 0;
};

}  // namespace devos::physics
