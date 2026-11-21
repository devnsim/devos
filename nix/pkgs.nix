# ==============================================================================
# FILE: nix/pkgs.nix
# OVERVIEW: The one way DevOS imports nixpkgs: the flake, default.nix and shell.nix all go through it, so they
#           share its settings.
# PURPOSE:
#   1. Imports the given nixpkgs for a system with DevOS's config: n8n (source-available, which nixpkgs counts as
#      unfree) is the only unfree package allowed.
# CONTRACT:
#   - Requires: nixpkgs (a source path or flake input), system (string).
#   - Exposes: the package set.
# NOTES:
#   - [2026-11-20] Created in Phase 6a (the n8n rule moved here from shell.nix).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None — relies solely on global README Lexicon.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PKGS
#    1.1 import
# ==============================================================================

# ==============================================================================
# [1.1] import
# ==============================================================================
{ nixpkgs, system ? builtins.currentSystem }:
import nixpkgs {
  inherit system;
  config.allowUnfreePredicate = pkg: (pkg.pname or (builtins.parseDrvName pkg.name).name) == "n8n";
}
# --- END [1.1] ---
