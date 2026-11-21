# ==============================================================================
# FILE: nix/checks.nix
# OVERVIEW: What `nix flake check` proves: the tests pass, the code standard holds, the built-in engines pass
#           the conformance kit, the physics engine passes its own tests, the installed app starts, and a fresh
#           NixOS machine with `programs.devos.enable = true;` runs DevOS as a normal user.
# PURPOSE:
#   1. tests: pytest over the repo (offscreen Qt, temporary data folder, the Nix-built native extension).
#   2. standard: tools/check_standard.py (no git in the sandbox, so the dated-notes rule is skipped there).
#   3. conformance: python -m engines.kernel.conformance on every engine package in the repo.
#   4. physics: the devos-physics package (its build runs ctest).
#   5. smoke: `devos --smoke-test` from the package, data in an empty home folder.
#   6. vm (x86_64-linux): a NixOS VM with the module: desktop entry installed, the smoke test as a normal
#      user, and the data folder created under that user's home.
# CONTRACT:
#   - Requires: pkgs (nix/pkgs.nix), packages (the flake's packages for this system), nixosModule.
#   - Exposes: an attribute set of check derivations.
# NOTES:
#   - [2026-11-20] Created in Phase 6a.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - repoSources: the repo without build output and bytecode (the checks run its own tools and tests).
# - headlessQt: environment for Qt and Qt WebEngine without a display or a GPU, inside the Nix sandbox.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. CHECKS
#    1.1 shared
#    1.2 repo_checks
#    1.3 package_checks
# ==============================================================================

{ pkgs, packages, nixosModule }:

# ==============================================================================
# [1.1] shared
# ==============================================================================
let
  lib = pkgs.lib;
  python = pkgs.python3.withPackages (import ./python.nix).development;
  notBytecode = file: !(file.hasExt "pyc");
  repoSources = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.difference (lib.fileset.fileFilter notBytecode ../.) (lib.fileset.unions [
      (lib.fileset.maybeMissing ../core/build)
      (lib.fileset.maybeMissing ../core/physics/build)
      (lib.fileset.maybeMissing ../.pytest_cache)
    ]);
  };
  qtPlugins = lib.makeSearchPath pkgs.qt6.qtbase.qtPluginPrefix [ pkgs.qt6.qtbase pkgs.qt6.qtsvg ];
  headlessQt = ''
    export HOME=$TMPDIR/home XDG_RUNTIME_DIR=$TMPDIR/runtime
    mkdir -p $HOME $XDG_RUNTIME_DIR && chmod 700 $XDG_RUNTIME_DIR
    export QT_QPA_PLATFORM=offscreen QT_PLUGIN_PATH=${qtPlugins}
    export QTWEBENGINE_DISABLE_SANDBOX=1 QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox"
  '';
  inRepo = name: script: pkgs.runCommand "devos-${name}" { nativeBuildInputs = [ python ]; } ''
    cp -r ${repoSources} src && chmod -R u+w src && cd src
    install -Dm755 -t core/build ${packages.devos-native}/lib/devos-native/devos_core*.so
    ${headlessQt}
    export DEVOS_DATA_DIR=$TMPDIR/data PYTHONDONTWRITEBYTECODE=1
    ${script}
    touch $out
  '';
# --- END [1.1] ---

# ==============================================================================
# [1.2] repo_checks
# ==============================================================================
in
{
  tests = inRepo "tests" "python -m pytest -q -p no:cacheprovider";

  standard = inRepo "standard" "python tools/check_standard.py";

  conformance = inRepo "conformance" ''
    for manifest in $(find engines examples -name engine.json | sort); do
      echo "== $(dirname $manifest)"
      python -m engines.kernel.conformance "$(dirname $manifest)"
    done
  '';
# --- END [1.2] ---

# ==============================================================================
# [1.3] package_checks
# ==============================================================================
  physics = packages.devos-physics;

  smoke = pkgs.runCommand "devos-smoke" { } ''
    ${headlessQt}
    ${lib.getExe packages.devos} --smoke-test | tee $TMPDIR/out
    grep -q "DevOS smoke test: ready" $TMPDIR/out
    test -s $HOME/.local/share/devos/devos.sqlite3      # the default data folder, outside the store
    touch $out
  '';
} // lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
  vm = pkgs.testers.runNixOSTest {
    name = "devos-vm";
    nodes.machine = {
      imports = [ nixosModule ];
      programs.devos.enable = true;
      users.users.dev = { isNormalUser = true; };
      virtualisation.memorySize = 3072;
    };
    testScript = ''
      machine.wait_for_unit("multi-user.target")
      machine.succeed("test -e /run/current-system/sw/share/applications/devos.desktop")
      out = machine.succeed(
          "su - dev -c 'QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu devos --smoke-test'")
      assert "DevOS smoke test: ready" in out, out
      machine.succeed("test -s /home/dev/.local/share/devos/devos.sqlite3")
    '';
  };
}
# --- END [1.3] ---
