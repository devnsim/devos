# ==============================================================================
# FILE: nix/pinned.nix
# OVERVIEW: The flake's pinned nixpkgs for Nix without flakes: reads flake.lock and fetches the same revision
#           (checked against its narHash), so nix-build / nix-shell build exactly what the flake builds.
# PURPOSE:
#   1. nixpkgs: the locked source (a store path).
#   2. pkgs: nix/pkgs.nix over it for the current system.
# CONTRACT:
#   - Requires: ../flake.lock with a github "nixpkgs" node.
#   - Exposes: { nixpkgs, pkgs }
# NOTES:
#   - [2026-11-20] Created in Phase 6a.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None — relies solely on global README Lexicon.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PIN
#    1.1 locked_nixpkgs
# ==============================================================================

# ==============================================================================
# [1.1] locked_nixpkgs
# ==============================================================================
let
  locked = (builtins.fromJSON (builtins.readFile ../flake.lock)).nodes.nixpkgs.locked;
  nixpkgs = builtins.fetchTarball {
    url = "https://github.com/${locked.owner}/${locked.repo}/archive/${locked.rev}.tar.gz";
    sha256 = locked.narHash;
  };
in
{
  inherit nixpkgs;
  pkgs = import ./pkgs.nix { inherit nixpkgs; };
}
# --- END [1.1] ---
