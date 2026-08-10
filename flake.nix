{
  description = "Self-hosted GitHub Actions runners running inside GitHub Actions";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
      };
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    let
      inherit (nixpkgs) lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSystems = lib.genAttrs systems;

      buildArgs = { inherit pyproject-nix uv2nix pyproject-build-systems; };
    in
    {
      overlays.default = final: _prev: {
        nested-runner = final.callPackage ./nix/package.nix buildArgs;
      };

      packages = forAllSystems (
        system:
        let
          nested-runner = nixpkgs.legacyPackages.${system}.callPackage ./nix/package.nix buildArgs;
        in
        {
          inherit nested-runner;
          default = nested-runner;
          inherit (nested-runner) keys venv;
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.actionlint
              pkgs.age
              pkgs.basedpyright
              pkgs.gh
              pkgs.just
              pkgs.python314
              pkgs.ruff
              pkgs.uv
              pkgs.yamllint
            ];
          };
        }
      );

      checks = forAllSystems (system: {
        inherit (self.packages.${system}) nested-runner keys venv;
      });

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);

      nixosModules.default = import ./nix/module.nix self;
    };
}
