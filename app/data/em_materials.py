# ==============================================================================
# FILE: app/data/em_materials.py
# OVERVIEW: Shared electromagnetic material library (1-10 GHz): permittivity, conductivity, reflection.
# PURPOSE:
#   1. Lists the materials the imager can recognise (MATERIALS), each with a frequency-dependent
#      permittivity / conductivity model and a display colour.
#   2. Computes complex relative permittivity and the normal-incidence reflection coefficient
#      (Fresnel) of each material at any frequency.
# CONTRACT:
#   - Requires: numpy
#   - Exposes: Material, MATERIALS (imaging classes; position = material id), EXTRA_MATERIALS,
#              ALL_MATERIALS, MATERIAL_BY_NAME (all), NO_MATERIAL_ID, SPEED_OF_LIGHT_M_S,
#              VACUUM_PERMITTIVITY_F_M
# NOTES:
#   - [2026-11-15] Created. Building materials follow the ITU-R P.2040 power-law model
#                  (eps' = a * f^b, sigma = c * f^d, f in GHz). Human tissue and water are rough fits
#                  to published dielectric data over 2.5-6 GHz, marked "approx." - refine them with
#                  measured data before trusting those two classes.
#   - [2026-11-15] Metal is treated as a perfect conductor (reflection coefficient -1).
#   - [2026-11-15] Moved from app/data/rf/materials.py (module apps live in app/modules/<name>/).
#   - [2026-11-15] Shared by rf_imaging and wave_sim. Added refractive_index() / loss_permittivity()
#                  and EXTRA_MATERIALS (PTFE) that are not RF-imaging classes, so the imaging
#                  classifier's MATERIALS tuple (and its ids) is unchanged.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - complex permittivity (eps_c): eps' - j * sigma / (2 pi f eps0); eps' stores energy, sigma loses it.
# - conductivity (sigma | S/m): How lossy the material is; higher = more absorption, frequency-dependent.
# - material id (int): Position in MATERIALS; NO_MATERIAL_ID (-1) = nothing / too weak to classify.
# - reflection coefficient (Gamma): Complex ratio of reflected to incident field at normal incidence,
#   Gamma = (1 - sqrt(eps_c)) / (1 + sqrt(eps_c)). |Gamma| = 0 (invisible) ... 1 (mirror, e.g. metal).
# - relative permittivity (eps' | unitless): How much the material slows the wave (air = 1).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. IMPORTS_AND_CONSTANTS
#    1.1 imports
#    1.2 physical_constants
# 2. MATERIAL
#    2.1 material_class
# 3. LIBRARY
#    3.1 materials
# ==============================================================================

# ==============================================================================
# [1.1] imports
# ==============================================================================
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
# --- END [1.1] ---

# ==============================================================================
# [1.2] physical_constants
# ==============================================================================
SPEED_OF_LIGHT_M_S = 299_792_458.0
VACUUM_PERMITTIVITY_F_M = 8.8541878128e-12
NO_MATERIAL_ID = -1
# --- END [1.2] ---


# ==============================================================================
# [2.1] material_class
# ==============================================================================
@dataclass(frozen=True)
class Material:
    """A material described by eps' = a * f_GHz^b and sigma = c * f_GHz^d (ITU-R P.2040 form)."""

    name: str
    permittivity_a: float
    permittivity_b: float
    conductivity_c: float
    conductivity_d: float
    color: tuple[float, float, float]          # RGB 0-1, used by the inspector
    is_conductor: bool = False
    source: str = "ITU-R P.2040"

    def complex_permittivity(self, frequency_hz: np.ndarray | float) -> np.ndarray:
        frequency_hz = np.asarray(frequency_hz, dtype=float)
        frequency_ghz = frequency_hz / 1e9
        permittivity_real = self.permittivity_a * frequency_ghz**self.permittivity_b
        conductivity_s_m = self.conductivity_c * frequency_ghz**self.conductivity_d
        return permittivity_real - 1j * conductivity_s_m / (2 * np.pi * frequency_hz * VACUUM_PERMITTIVITY_F_M)

    def refractive_index(self, frequency_hz: np.ndarray | float) -> np.ndarray:
        """Complex refractive index n = sqrt(eps_c) = n' - j n'' (n'' >= 0: absorption)."""
        return np.sqrt(self.complex_permittivity(frequency_hz))

    def loss_permittivity(self, frequency_hz: np.ndarray | float) -> np.ndarray:
        """eps'' = sigma / (2 pi f eps0), the absorbing part of the permittivity."""
        return -np.imag(self.complex_permittivity(frequency_hz))

    def reflection_coefficient(self, frequency_hz: np.ndarray | float) -> np.ndarray:
        """Normal-incidence Fresnel reflection coefficient (complex) at each frequency."""
        frequency_hz = np.asarray(frequency_hz, dtype=float)
        if self.is_conductor:
            return -np.ones(frequency_hz.shape, dtype=complex)
        refractive_index = np.sqrt(self.complex_permittivity(frequency_hz))
        return (1 - refractive_index) / (1 + refractive_index)
# --- END [2.1] ---


# ==============================================================================
# [3.1] materials
# ==============================================================================
MATERIALS: tuple[Material, ...] = (
    Material("Plasterboard", 2.73, 0.0, 0.0085, 0.9395, (0.85, 0.85, 0.80)),
    Material("Wood", 1.99, 0.0, 0.0047, 1.0718, (0.80, 0.55, 0.25)),
    Material("Brick", 3.91, 0.0, 0.0238, 0.16, (0.80, 0.35, 0.25)),
    Material("Concrete", 5.24, 0.0, 0.0462, 0.7822, (0.60, 0.60, 0.65)),
    Material("Glass", 6.31, 0.0, 0.0036, 1.3394, (0.45, 0.80, 0.90)),
    Material("Human tissue", 57.4, -0.0964, 0.586, 1.216, (0.95, 0.45, 0.60), source="approx. (muscle, 2.45/5.8 GHz fit)"),
    Material("Water", 79.0, -0.03, 0.35, 1.80, (0.25, 0.45, 0.95), source="approx. (Debye fit, 25 C)"),
    Material("Metal", 1.0, 0.0, 1e7, 0.0, (1.00, 0.85, 0.20), is_conductor=True, source="perfect conductor"),
)
# Not RF-imaging classes (kept out of MATERIALS so classifier ids stay stable).
EXTRA_MATERIALS: tuple[Material, ...] = (
    # PTFE: eps' ~2.1, loss tangent ~2e-4 -> sigma = 2 pi f eps0 eps' tan_d ~ 2.3e-5 * f_GHz S/m
    Material("PTFE / plastic", 2.1, 0.0, 2.3e-5, 1.0, (0.92, 0.92, 0.95), source="PTFE datasheet (tan d 2e-4)"),
)
ALL_MATERIALS: tuple[Material, ...] = MATERIALS + EXTRA_MATERIALS
MATERIAL_BY_NAME = {material.name: material for material in ALL_MATERIALS}
# --- END [3.1] ---
