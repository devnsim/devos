# ==============================================================================
# FILE: default.nix
# OVERVIEW: DevOS for Nix without flakes: `nix-build` builds the same pinned packages as the flake.
# PURPOSE:
#   1. nix-build                      -> ./result/bin/devos
#   2. nix-build -A devos-physics     (also: devos-native)
#   3. nix-env -if . / imports in a configuration that doesn't use flakes.
# CONTRACT:
#   - Requires: flake.lock (the pin, read by nix/pinned.nix).
#   - Exposes: { devos, devos-native, devos-physics } (devos is the default derivation's first attribute).
# NOTES:
#   - [2026-11-20] Created in Phase 6a.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None — relies solely on global README Lexicon.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PACKAGES
#    1.1 attributes
# ==============================================================================

# ==============================================================================
# [1.1] attributes
# ==============================================================================
{ pkgs ? (import ./nix/pinned.nix).pkgs }:
rec {
  devos-native = pkgs.callPackage ./nix/native.nix { };
  devos-physics = pkgs.callPackage ./nix/physics.nix { };
  devos = pkgs.callPackage ./nix/package.nix { inherit devos-native; };
}
# --- END [1.1] ---
