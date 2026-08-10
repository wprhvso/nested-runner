self:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    all
    concatMapStringsSep
    escapeShellArg
    filterAttrs
    literalExpression
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  cfg = config.services.nested-runner;

  repoPattern = "[^/[:space:]]+/[^/[:space:]]+";

  settings = filterAttrs (_name: value: value != null) (
    {
      GH_REPO = cfg.homeRepo;
      NESTED_SCALE_SET = cfg.scaleSet;
      NESTED_MAX = toString cfg.maxRunners;
      NESTED_WORKFLOW = cfg.workflow;
      NESTED_DEBUG = if cfg.debug then "1" else null;
      GITHUB_API_URL = cfg.apiUrl;
      GITHUB_SERVER_URL = cfg.serverUrl;
      HOME = "%S/nested-runner";
      SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
    }
    // cfg.extraEnvironment
  );

  hardening = {
    AmbientCapabilities = [ "" ];
    CapabilityBoundingSet = [ "" ];
    DevicePolicy = "closed";
    LockPersonality = true;
    NoNewPrivileges = true;
    PrivateDevices = true;
    PrivateTmp = true;
    ProtectClock = true;
    ProtectControlGroups = true;
    ProtectHome = true;
    ProtectHostname = true;
    ProtectKernelLogs = true;
    ProtectKernelModules = true;
    ProtectKernelTunables = true;
    ProtectProc = "invisible";
    ProtectSystem = "strict";
    RestrictAddressFamilies = [
      "AF_INET"
      "AF_INET6"
      "AF_NETLINK"
      "AF_UNIX"
    ];
    RestrictNamespaces = true;
    RestrictRealtime = true;
    RestrictSUIDSGID = true;
    SystemCallArchitectures = "native";
    SystemCallFilter = [
      "@system-service"
      "~@privileged"
    ];
    UMask = "0077";
  };
in
{
  options.services.nested-runner = {
    enable = mkEnableOption "the nested GitHub Actions runner controller";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = literalExpression "nested-runner.packages.\${system}.default";
      description = "Package providing the controller and the age public key it dispatches with.";
    };

    repos = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "wprhvso/ai" ];
      description = "Target repositories a scale set is kept alive for, as `owner/name`.";
    };

    homeRepo = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "wprhvso/nested-runner";
      description = ''
        Repository the runner workflow is dispatched in. Defaults to the one
        baked into the controller when unset.
      '';
    };

    scaleSet = mkOption {
      type = types.str;
      default = "nested";
      description = "Name of the runner scale set registered in every target repository.";
    };

    maxRunners = mkOption {
      type = types.ints.positive;
      default = 20;
      description = "Largest number of runners alive at once per target repository.";
    };

    workflow = mkOption {
      type = types.str;
      default = "runner.yml";
      description = "Workflow in the home repository that hosts a single runner.";
    };

    debug = mkOption {
      type = types.bool;
      default = false;
      description = "Whether to log every queue message and dispatch.";
    };

    apiUrl = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "https://api.github.com";
      description = "GitHub REST API base, for GitHub Enterprise deployments.";
    };

    serverUrl = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "https://github.com";
      description = "GitHub web base, for GitHub Enterprise deployments.";
    };

    environmentFiles = mkOption {
      type = types.listOf types.path;
      default = [ ];
      example = [ "/var/lib/secrets/nested-runner" ];
      description = ''
        Files holding the secrets (GH_TOKEN, with the `repo` and `workflow`
        scopes). Values defined here take precedence over the generated
        environment.
      '';
    };

    extraEnvironment = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Extra environment variables merged into the generated environment.";
    };

    gracefulShutdownTimeout = mkOption {
      type = types.ints.unsigned;
      default = 120;
      description = ''
        Seconds the controller is given to tear down its scale sets, cancel the
        runs it started and drop the runners it registered.
      '';
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.repos != [ ];
        message = "services.nested-runner.repos must name at least one target repository.";
      }
      {
        assertion = all (repo: builtins.match repoPattern repo != null) cfg.repos;
        message = "services.nested-runner.repos takes owner/name, nothing else.";
      }
      {
        assertion = cfg.environmentFiles != [ ];
        message = ''
          services.nested-runner.environmentFiles must provide GH_TOKEN; the
          controller has nothing to talk to GitHub with otherwise.
        '';
      }
    ];

    systemd.services.nested-runner = {
      description = "Nested GitHub Actions runner controller";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = settings;

      path = [
        pkgs.age
        pkgs.gh
      ];

      serviceConfig = hardening // {
        Type = "exec";
        DynamicUser = true;
        StateDirectory = "nested-runner";
        StateDirectoryMode = "0700";
        EnvironmentFile = cfg.environmentFiles;
        WorkingDirectory = cfg.package.keys;
        ExecStart = "${cfg.package}/bin/nested-runner ${concatMapStringsSep " " escapeShellArg cfg.repos}";
        Restart = "always";
        RestartSec = "10s";
        TimeoutStopSec = "${toString (cfg.gracefulShutdownTimeout + 30)}s";
      };
    };
  };
}
