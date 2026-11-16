# ==============================================================================
# FILE: app/modules/graph/datasets.py
# OVERVIEW: Datasets for the Graph tool (and anything else that plots tables): load, save, import and
#           generate mock data. Datasets are CSV files in the user's content library.
# PURPOSE:
#   1. load_dataset(item_id) -> Dataset (column names + float table); bad files raise DatasetError.
#   2. save_dataset(name, columns, data) -> content item; import_csv(path) (validated on import).
#   3. MOCK_GENERATORS: sine, random walk, noise, sawtooth, damped oscillation, exponential growth.
# CONTRACT:
#   - Requires: numpy, app.data.store.content (get_content_store), app.data.activity_log
#   - Exposes: Dataset, DatasetError, load_dataset(), save_dataset(), import_csv(), make_mock(), MOCK_GENERATORS
# NOTES:
#   - [2026-11-15] Created with the Graph tool. CSV: first row = column names, numbers after; non-numeric
#                  cells become NaN (gaps in the line). Every load / import / mock is announced in the log.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - dataset: A named table of numbers (columns x rows) stored as CSV in the content library (kind "dataset").
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. DATASETS
#    1.1 imports_and_types
#    1.2 load_and_save
#    1.3 mock_generators
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.data.activity_log import get_activity_log
from app.data.store.content import get_content_store


class DatasetError(Exception):
    """A dataset file that can't be read as a table of numbers."""


@dataclass
class Dataset:
    item_id: int | None
    name: str
    columns: list[str]
    data: np.ndarray                 # (rows, columns) float

    def column(self, name: str) -> np.ndarray:
        return self.data[:, self.columns.index(name)]
# --- END [1.1] ---

# ==============================================================================
# [1.2] load_and_save
# ==============================================================================
def _parse_csv(text: str) -> tuple[list[str], np.ndarray]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise DatasetError("needs a header row and at least one data row")
    columns = [name.strip() or f"column {index + 1}" for index, name in enumerate(lines[0].split(","))]
    data = np.genfromtxt(io.StringIO("\n".join(lines[1:])), delimiter=",", dtype=float, ndmin=2)
    if data.shape[1] != len(columns):
        raise DatasetError(f"header has {len(columns)} columns but rows have {data.shape[1]}")
    return columns, data


def load_dataset(item_id: int) -> Dataset:
    store = get_content_store()
    item = store.get(item_id)
    if item is None:
        raise DatasetError(f"dataset #{item_id} no longer exists")
    with get_activity_log().step("graph", f"Load dataset “{item.name}”", level=20):
        try:
            columns, data = _parse_csv(store.read_text(item_id))
        except (OSError, ValueError) as error:
            raise DatasetError(str(error)) from error
    get_activity_log().info("graph", f"Dataset “{item.name}”: {data.shape[0]:,} rows × {len(columns)} columns")
    return Dataset(item_id, item.name, columns, data)


def save_dataset(name: str, columns: list[str], data: np.ndarray, metadata: dict | None = None):
    buffer = io.StringIO()
    buffer.write(",".join(columns) + "\n")
    np.savetxt(buffer, np.asarray(data, float), delimiter=",", fmt="%.10g")
    store = get_content_store()
    return store.add_bytes("dataset", store.unique_name("dataset", name), buffer.getvalue().encode(), ".csv", metadata)


def import_csv(path: str | Path):
    """Copy a CSV into the library after checking it parses."""
    path = Path(path)
    with get_activity_log().step("graph", f"Import {path.name}"):
        try:
            _parse_csv(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as error:
            raise DatasetError(f"{path.name}: {error}") from error
        return get_content_store().add_file("dataset", path)
# --- END [1.2] ---

# ==============================================================================
# [1.3] mock_generators
# ==============================================================================
def _sine(x, amplitude, frequency, rng):
    return amplitude * np.sin(2 * np.pi * frequency * x)


def _random_walk(x, amplitude, frequency, rng):
    return amplitude * np.cumsum(rng.normal(0, 1, len(x))) / np.sqrt(len(x))


def _noise(x, amplitude, frequency, rng):
    return amplitude * rng.normal(0, 1, len(x))


def _sawtooth(x, amplitude, frequency, rng):
    return amplitude * (2 * ((frequency * x) % 1) - 1)


def _damped(x, amplitude, frequency, rng):
    return amplitude * np.exp(-x / max(x[-1], 1e-9) * 3) * np.cos(2 * np.pi * frequency * x)


def _growth(x, amplitude, frequency, rng):
    return amplitude * np.exp(frequency * x / max(x[-1], 1e-9))


MOCK_GENERATORS = {
    "Sine wave": _sine, "Random walk": _random_walk, "Noise": _noise, "Sawtooth": _sawtooth,
    "Damped oscillation": _damped, "Exponential growth": _growth,
}


def make_mock(kind: str, points: int = 500, amplitude: float = 1.0, frequency: float = 1.0,
              noise: float = 0.0, seed: int = 1) -> tuple[list[str], np.ndarray]:
    """Columns ["x", "y"]; x runs 0..10."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0.0, 10.0, points)
    y = MOCK_GENERATORS[kind](x, amplitude, frequency, rng) + noise * rng.normal(0, 1, points)
    return ["x", "y"], np.column_stack([x, y])
# --- END [1.3] ---
