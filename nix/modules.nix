# ==============================================================================
# FILE: nix/modules.nix
# OVERVIEW: The NixOS module and the home-manager module that install DevOS on a machine:
#           `programs.devos.enable = true;` puts `devos` on the PATH and DevOS in the application menu.
# PURPOSE:
#   1. nixos: programs.devos.{enable, package, withN8n} -> environment.systemPackages.
#   2. homeManager: the same options -> home.packages (for one user, no system rebuild).
#   Both default to the flake's own pinned build, so every machine runs the same DevOS whatever nixpkgs it
#   follows; set `package` to build it against the machine's nixpkgs instead.
# CONTRACT:
#   - Requires: the flake's `packages` (passed in by flake.nix).
#   - Exposes: { nixos, homeManager } - module functions.
# NOTES:
#   - [2026-11-20] Created in Phase 6a.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - flakePackages: the flake's packages attribute set, per system (packages.<system>.devos ...).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. MODULES
#    1.1 options
#    1.2 nixos_and_home_manager
# ==============================================================================

flakePackages:

# ==============================================================================
# [1.1] options
# ==============================================================================
let
  options = { lib, pkgs }:
    let packages = flakePackages.${pkgs.stdenv.hostPlatform.system}; in {
      enable = lib.mkEnableOption "DevOS, the data desktop";
      withN8n = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Bundle n8n (large; unfree Sustainable Use License) for the Web App's n8n preset.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = packages.devos;
        defaultText = lib.literalExpression "devos.packages.\${system}.devos";
        description = "The DevOS package (the flake's pinned build by default).";
      };
    };

  devosPackage = cfg: if cfg.withN8n then cfg.package.override { withN8n = true; } else cfg.package;
# --- END [1.1] ---

# ==============================================================================
# [1.2] nixos_and_home_manager
# ==============================================================================
in
{
  nixos = { config, lib, pkgs, ... }:
    let cfg = config.programs.devos; in {
      options.programs.devos = options { inherit lib pkgs; };
      config = lib.mkIf cfg.enable {
        environment.systemPackages = [ (devosPackage cfg) ];
      };
    };

  homeManager = { config, lib, pkgs, ... }:
    let cfg = config.programs.devos; in {
      options.programs.devos = options { inherit lib pkgs; };
      config = lib.mkIf cfg.enable {
        home.packages = [ (devosPackage cfg) ];
      };
    };
}
# --- END [1.2] ---
