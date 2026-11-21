# ==============================================================================
# FILE: nix/nixos.nix
# OVERVIEW: The DevOS NixOS module for configurations that don't use flakes (a plain
#           /etc/nixos/configuration.nix): import this file, then `programs.devos.enable = true;`.
# PURPOSE:
#   1. The same module as the flake's nixosModules.default (nix/modules.nix), with the packages built from the
#      same pins (default.nix over nix/pinned.nix), so both routes install the identical DevOS.
#   Usage in configuration.nix:
#     imports = [ "${builtins.fetchTarball "https://github.com/devnsim/devos/archive/<rev>.tar.gz"}/nix/nixos.nix" ];
#     programs.devos.enable = true;
# CONTRACT:
#   - Requires: ../default.nix, ./pkgs.nix, ./pinned.nix, ./modules.nix.
#   - Exposes: a NixOS module (programs.devos.*).
# NOTES:
#   - [2026-11-20] Created in Phase 6a: a new machine with a plain configuration.nix couldn't use the flake-only
#                  module ("programs.devos does not exist").
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - packagesFor: the DevOS packages for one system, from the flake's pinned nixpkgs.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODULE
#    1.1 packages_and_module
# ==============================================================================

# ==============================================================================
# [1.1] packages_and_module
# ==============================================================================
let
  nixpkgs = (import ./pinned.nix).nixpkgs;
  packagesFor = system: import ../default.nix { pkgs = import ./pkgs.nix { inherit nixpkgs system; }; };
  systems = [ "x86_64-linux" "aarch64-linux" ];
  flakeLikePackages = builtins.listToAttrs (map (system: { name = system; value = packagesFor system; }) systems);
in
(import ./modules.nix flakeLikePackages).nixos
# --- END [1.1] ---
