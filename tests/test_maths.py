# ==============================================================================
# FILE: tests/test_maths.py
# OVERVIEW: Numerical tests for the engines: native vs NumPy, indicators, backtester, RF physics.
# PURPOSE:
#   1. C++ and NumPy engines agree (when the C++ engine is built).
#   2. Indicators match plain-loop references and never look ahead; the backtester matches a hand
#      calculation.
#   3. RF imaging images a point target exactly; the wave simulator matches free-space and slab physics.
# CONTRACT:
#   - Requires: numpy, trimesh, app.native, app.modules (trading_chart, rf_imaging, wave_sim), app.data
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-15] Created in the health pass from the checks run while building each engine.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - reference loop: Slow, obviously-correct Python implementation a vectorised one is compared with.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TESTS
#    1.1 native_engine
#    1.2 trading_maths
#    1.3 rf_physics
# ==============================================================================

import numpy as np
import pytest
import trimesh


# ==============================================================================
# [1.1] native_engine
# ==============================================================================
def test_native_matches_numpy():
    from app import native
    if not native.HAS_NATIVE:
        pytest.skip("C++ engine not built (python -m app.native.build)")
    result = native.self_test()
    assert result["max_inside_difference_m"] < 1e-9
    assert result["crossing_mismatches"] == 0
    assert result["max_response_relative_difference"] < 1e-12
    assert result["max_ema_relative_difference"] < 1e-12


def test_numpy_ema_matches_loop():
    from app.native.numpy_engine import ema
    values = np.cumsum(np.random.default_rng(1).normal(size=3000)) + 50
    for alpha in (0.9, 0.5, 0.1, 0.01):
        state, expected = values[0], []
        for value in values:
            state += alpha * (value - state)
            expected.append(state)
        assert np.allclose(ema(values, alpha), expected, rtol=1e-12)
# --- END [1.1] ---

# ==============================================================================
# [1.2] trading_maths
# ==============================================================================
def test_indicators_match_loops_and_never_look_ahead():
    from app.modules.trading_chart.indicators import ema, macd, rsi, sma
    from app.modules.trading_chart.market_data import MockBitcoinFeed
    close = MockBitcoinFeed().history(10).resample(3600).close
    reference_sma = [close[i - 19:i + 1].mean() for i in range(19, len(close))]
    assert np.allclose(sma(close, 20)[19:], reference_sma, rtol=1e-9)
    alpha, state, reference_ema = 2 / 22, close[0], []
    for value in close:
        state += alpha * (value - state)
        reference_ema.append(state)
    assert np.allclose(ema(close, 21)[20:], reference_ema[20:])
    future_changed = close.copy()
    future_changed[-30:] *= 1.5
    for function in (lambda c: ema(c, 21), lambda c: rsi(c, 14), lambda c: macd(c)["macd"]):
        assert np.array_equal(function(close)[:-30], function(future_changed)[:-30], equal_nan=True)


def test_backtest_matches_hand_calculation():
    from app.modules.trading_chart.backtest import run_backtest
    from app.modules.trading_chart.market_data import BarSeries
    bars = BarSeries(np.arange(4) * 3600, np.array([100., 110, 120, 130]), np.array([101., 111, 121, 131]),
                     np.array([99., 109, 119, 129]), np.array([105., 115, 125, 135]), np.ones(4))
    result = run_backtest(bars, np.array([1, 1, 0, 0]), 3600, fee_rate=0.001, initial_equity=1000)
    assert result.equity[-1] == pytest.approx(1000 * 0.999 * (115 / 110) * (125 / 115) * (130 / 125) * 0.999)
    assert (result.trades[0].entry_price, result.trades[0].exit_price) == (110.0, 130.0)


def test_mock_feed_is_deterministic_and_consistent():
    from app.modules.trading_chart.market_data import MockBitcoinFeed
    first, second = MockBitcoinFeed().history(3), MockBitcoinFeed().history(3)
    count = min(len(first), len(second)) - 2
    assert np.array_equal(first.close[:count], second.close[:count])
    assert np.all(first.high >= np.maximum(first.open, first.close))
    assert np.all(first.low <= np.minimum(first.open, first.close))
# --- END [1.2] ---

# ==============================================================================
# [1.3] rf_physics
# ==============================================================================
def test_rf_point_target_images_exactly():
    from app.modules.rf_imaging.imaging import Backprojector, VoxelGrid
    from app.modules.rf_imaging.sensor import AntennaArray, SweepConfig, simulate_response
    config, array, grid = SweepConfig(bin_count=32), AntennaArray.l_shaped(4), VoxelGrid(spacing_m=0.2)
    backprojector = Backprojector(config, array, grid)
    voxel = (5, 3, 4)
    response = simulate_response(grid.index_to_m(voxel)[None], np.array([0.4 + 0j]), config, array)
    image = np.abs(backprojector.combine(backprojector.image_bands(response))).reshape(grid.shape)
    assert np.unravel_index(image.argmax(), grid.shape) == voxel
    assert image.max() == pytest.approx(0.4, rel=1e-4)


def test_wave_sim_free_space_and_slab():
    from app.data.em_materials import MATERIAL_BY_NAME, SPEED_OF_LIGHT_M_S
    from app.modules.wave_sim.engine import WaveSimEngine
    from app.modules.wave_sim.physics import SweepSettings
    from app.modules.wave_sim.scene import Body, Scene
    sweep = SweepSettings(bin_count=32)
    k = 2 * np.pi * sweep.frequencies_hz() / SPEED_OF_LIGHT_M_S
    free = WaveSimEngine(Scene([], np.array([0, 0, 1.0]), np.array([[3.0, 0, 1.0]]), ["rx"]), sweep, MATERIAL_BY_NAME)
    free.NOISE_FLOOR = 0
    assert np.allclose(free.step(0).response[0], np.exp(-1j * k * 3) / 3, rtol=1e-12)
    slab = Body.from_trimesh("slab", trimesh.creation.box(extents=(0.12, 2, 2)).apply_translation((1.5, 0, 1.0)), "Plasterboard")
    engine = WaveSimEngine(Scene([slab], np.array([0, 0, 1.0]), np.array([[3.0, 0, 1.0]]), ["rx"]), sweep, MATERIAL_BY_NAME)
    paths = engine.step(0).paths
    direct = paths.reflect_body < 0
    assert paths.inside_m[direct][0][0] == pytest.approx(0.12)
    assert paths.crossings[direct][0][0] == 2
# --- END [1.3] ---
