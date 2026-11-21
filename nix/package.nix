# ==============================================================================
# FILE: nix/package.nix
# OVERVIEW: DevOS itself as a Nix package: the app and engine sources, the prebuilt native extension, a `devos`
#           launcher that finds Qt's plugins, and a desktop entry with the icon. Runs from the read-only store;
#           your data stays in ~/.local/share/devos (DEVOS_DATA_DIR), exactly as from a checkout.
# PURPOSE:
#   1. share/devos/: app/, engines/, assets/ (the layout app/ expects: icons and built-in engines are found
#      relative to the sources) plus core/build/devos_core*.so from devos-native, byte-compiled.
#   2. bin/devos: Python (nix/python.nix runtime list) + `-m app.main`, wrapped with Qt's plugin paths;
#      optional tools (n8n, LibreOffice, Thunderbird) are looked up on the user's PATH, appended after ours.
#   3. share/applications/devos.desktop (StartupWMClass devos, matching app.setDesktopFileName) and the icon.
# CONTRACT:
#   - Requires: callPackage arguments; devos-native (nix/native.nix); withN8n (default false) bundles n8n.
#   - Exposes: the derivation (mainProgram devos); passthru.python (the interpreter, for checks).
# NOTES:
#   - [2026-11-20] Created in Phase 6a (replaces run.sh + install-desktop.sh outside a checkout).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - appSources: the folders of the repo the running app needs (tests, docs and C++ sources are left out).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PACKAGE
#    1.1 inputs
#    1.2 derivation
# ==============================================================================

{ lib, stdenv, python3, qt6, makeWrapper, makeDesktopItem, copyDesktopItems, devos-native
, n8n ? null, withN8n ? false }:

# ==============================================================================
# [1.1] inputs
# ==============================================================================
let
  python = python3.withPackages (import ./python.nix).runtime;
  notBytecode = file: !(file.hasExt "pyc");
  appSources = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      (lib.fileset.fileFilter notBytecode ../app)
      (lib.fileset.fileFilter notBytecode ../engines)
      ../assets
    ];
  };
  extraPath = lib.optionals (withN8n && n8n != null) [ n8n ];
# --- END [1.1] ---

# ==============================================================================
# [1.2] derivation
# ==============================================================================
in
stdenv.mkDerivation {
  pname = "devos";
  version = "0.3.0";
  src = appSources;

  nativeBuildInputs = [ makeWrapper qt6.wrapQtAppsHook copyDesktopItems ];
  buildInputs = [ qt6.qtbase qt6.qtwayland qt6.qtsvg ];
  dontBuild = true;
  dontWrapQtApps = true;              # we wrap the Python launcher ourselves, with qtWrapperArgs

  desktopItems = [
    (makeDesktopItem {
      name = "devos";
      desktopName = "DevOS";
      comment = "DevOS control dashboard";
      exec = "devos";
      icon = "devos";
      startupWMClass = "devos";
      categories = [ "Development" "Utility" ];
    })
  ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/devos
    cp -r app engines assets $out/share/devos/
    install -Dm755 -t $out/share/devos/core/build ${devos-native}/lib/devos-native/devos_core*.so
    ${python.interpreter} -m compileall -q $out/share/devos/app $out/share/devos/engines
    install -Dm644 assets/devos.svg $out/share/icons/hicolor/scalable/apps/devos.svg
    runHook postInstall
  '';

  postFixup = ''
    makeWrapper ${python.interpreter} $out/bin/devos \
      --add-flags "-m app.main" \
      --prefix PYTHONPATH : $out/share/devos \
      ${lib.optionalString (extraPath != [ ]) "--prefix PATH : ${lib.makeBinPath extraPath}"} \
      "''${qtWrapperArgs[@]}"
  '';

  passthru = { inherit python; };

  meta = {
    description = "DevOS: a desktop for organising and viewing any kind of data, with dockable engines";
    platforms = lib.platforms.linux;
    mainProgram = "devos";
  };
}
# --- END [1.2] ---
