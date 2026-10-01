{
  description = "Pratevenn: a local conversational AI tool for Norwegian language learners";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      # Current nixpkgs no longer supports Intel macOS.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          name = "pratevenn-dev";

          packages = with pkgs; [
            python314
            uv
            gnumake
            stdenv.cc
            cmake
            ninja
          ];

          UV_PYTHON = "${pkgs.python314}/bin/python3";
          UV_PYTHON_DOWNLOADS = "never";

          # Native wheels need runtime libraries and access to the host GPU driver.
          shellHook = pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
            export LD_LIBRARY_PATH="${
              pkgs.lib.makeLibraryPath [
                pkgs.stdenv.cc.cc.lib
                pkgs.zlib
              ]
            }:/run/opengl-driver/lib:/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          '';
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
