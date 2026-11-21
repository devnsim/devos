# ==============================================================================
# FILE: flake.nix
# OVERVIEW: DevOS as a reproducible flake: the packages, the launcher, the development shell, the NixOS and
#           home-manager modules, and the checks that prove it all works - every input pinned in flake.lock.
# PURPOSE:
#   1. packages.<system>: devos (default), devos-native (core/src maths), devos-physics (core/physics).
#   2. apps.<system>.default: `nix run github:<owner>/devos` starts DevOS on any NixOS (or Nix) machine.
#   3. devShells.<system>.default: today's development shell (shell.nix, on the pinned nixpkgs).
#   4. nixosModules.default / homeManagerModules.default: `programs.devos.enable = true;` (nix/modules.nix).
#   5. overlays.default: adds devos, devos-native and devos-physics to a nixpkgs.
#   6. checks.<system>: tests (pytest), standard (tools/check_standard.py), conformance (built-in engines),
#      physics (ctest inside its build), smoke (the installed app starts), vm (a fresh NixOS VM with the
#      module enabled starts DevOS as a normal user; x86_64-linux).
# CONTRACT:
#   - Requires: nixpkgs (pinned; same revision as the dev machine's channel when it was added).
#   - Exposes: the outputs above. Without flakes: default.nix (nix-build) and shell.nix (nix-shell) use the same pins.
# NOTES:
#   - [2026-11-20] Created in Phase 6a (reproducible DevOS on any NixOS machine).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - systems: the platforms DevOS builds for (x86_64-linux, aarch64-linux).
# - repoSources: every tracked file except build output, for checks that run the repo's own tools.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. FLAKE
#    1.1 inputs
#    1.2 packages_and_shell
#    1.3 checks
#    1.4 modules_and_overlay
# ==============================================================================

{
  description = "DevOS: a desktop for organising and viewing any kind of data, with dockable engines";

# ==============================================================================
# [1.1] inputs
# ==============================================================================
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/d57af924f160a5084293c71c2043f058bd1cdb60";
# --- END [1.1] ---

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forSystems = f: lib.genAttrs systems (system: f (import ./nix/pkgs.nix { inherit nixpkgs system; }));
      modules = import ./nix/modules.nix self.packages;

# ==============================================================================
# [1.2] packages_and_shell
# ==============================================================================
      devosPackages = pkgs: rec {
        devos-native = pkgs.callPackage ./nix/native.nix { };
        devos-physics = pkgs.callPackage ./nix/physics.nix { };
        devos = pkgs.callPackage ./nix/package.nix { inherit devos-native; };
        default = devos;
      };
# --- END [1.2] ---
    in
    {
      packages = forSystems devosPackages;

      apps = forSystems (pkgs: {
        default = { type = "app"; program = lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.devos; };
      });

      devShells = forSystems (pkgs: { default = import ./shell.nix { inherit pkgs; }; });

# ==============================================================================
# [1.3] checks
# ==============================================================================
      checks = forSystems (pkgs: import ./nix/checks.nix {
        inherit pkgs;
        packages = self.packages.${pkgs.stdenv.hostPlatform.system};
        nixosModule = self.nixosModules.default;
      });
# --- END [1.3] ---

# ==============================================================================
# [1.4] modules_and_overlay
# ==============================================================================
      nixosModules.default = modules.nixos;
      homeManagerModules.default = modules.homeManager;
      overlays.default = final: prev: builtins.removeAttrs (devosPackages final) [ "default" ];
# --- END [1.4] ---
    };
}
