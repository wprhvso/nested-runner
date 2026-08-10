{
  lib,
  stdenvNoCC,
  callPackage,
  callPackages,
  python314,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
  sourcePreference ? "wheel",
}:

let
  root = ../.;

  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = root; };

  sourceOverrides = _final: prev: {
    nested-runner = prev.nested-runner.overrideAttrs (old: {
      src = lib.fileset.toSource {
        root = old.src;
        fileset = lib.fileset.unions [
          (old.src + "/pyproject.toml")
          (lib.fileset.maybeMissing (old.src + "/README.md"))
          (old.src + "/nested_runner")
        ];
      };
    });
  };

  pythonSet = (callPackage pyproject-nix.build.packages { python = python314; }).overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      (workspace.mkPyprojectOverlay { inherit sourcePreference; })
      sourceOverrides
    ]
  );

  venv = pythonSet.mkVirtualEnv "nested-runner-env" workspace.deps.default;

  # The controller reads the age recipient from `keys/nested.pub` next to its
  # working directory, so the key ships as its own directory to point at.
  keys = stdenvNoCC.mkDerivation {
    pname = "nested-runner-keys";
    inherit (pythonSet.nested-runner) version;

    src = lib.fileset.toSource {
      inherit root;
      fileset = root + "/keys";
    };

    dontConfigure = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -r keys "$out/keys"
      runHook postInstall
    '';
  };

  inherit (callPackages pyproject-nix.build.util { }) mkApplication;
in
(mkApplication {
  inherit venv;
  package = pythonSet.nested-runner;
}).overrideAttrs
  (old: {
    passthru = (old.passthru or { }) // {
      inherit keys venv;
      inherit (pythonSet.nested-runner) version;
    };

    meta = (old.meta or { }) // {
      description = "Self-hosted GitHub Actions runners running inside GitHub Actions";
      mainProgram = "nested-runner";
      platforms = lib.platforms.linux;
    };
  })
