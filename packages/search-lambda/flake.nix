{
  description = "fdnix search lambda - Rust AWS Lambda with DuckDB";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        overlays = [ (import rust-overlay) ];
        pkgs = import nixpkgs { inherit system overlays; };
        
        # Use musl cross-compilation for static linking (Lambda requirement)
        muslPkgs = pkgs.pkgsCross.musl64;
        
        # Rust toolchain with musl target
        rustToolchain = pkgs.rust-bin.stable.latest.default.override {
          targets = [ "x86_64-unknown-linux-musl" ];
        };

        # Native build tools (use host system packages)
        nativeBuildInputs = with pkgs; [
          rustToolchain
          clang
          llvmPackages.libclang.lib
          llvmPackages.libcxxClang
          cmake
          ninja
          pkg-config
        ];

        # Target libraries for musl cross-compilation
        buildInputs = with muslPkgs; [
          stdenv.cc
        ];

        # Environment variables for cross-compilation and bindgen
        buildEnv = {
          # Bindgen requirements
          LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
          BINDGEN_EXTRA_CLANG_ARGS = builtins.concatStringsSep " " [
            "-I${pkgs.llvmPackages.libclang.lib}/lib/clang/${pkgs.llvmPackages.libclang.version}/include"
            "-I${muslPkgs.stdenv.cc.cc}/include/c++/${muslPkgs.stdenv.cc.cc.version}"
            "-I${muslPkgs.stdenv.cc.cc}/include/c++/${muslPkgs.stdenv.cc.cc.version}/x86_64-unknown-linux-musl"
          ];
          
          # Cross-compilation setup
          CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER = "${muslPkgs.stdenv.cc}/bin/${muslPkgs.stdenv.cc.targetPrefix}cc";
          CC_x86_64_unknown_linux_musl = "${muslPkgs.stdenv.cc}/bin/${muslPkgs.stdenv.cc.targetPrefix}cc";
          CXX_x86_64_unknown_linux_musl = "${muslPkgs.stdenv.cc}/bin/${muslPkgs.stdenv.cc.targetPrefix}c++";
          
          # DuckDB static build configuration
          DUCKDB_STATIC_BUILD = "1";
          BUILD_EXTENSIONS = "json;fts;vss;core_functions;jemalloc";
          DISABLE_BUILTIN_EXTENSIONS = "0";
          STATIC_LIBCPP = "1";
          
          # Rust compilation flags for static linking
          RUSTFLAGS = "-C target-feature=+crt-static -C link-arg=-static";
        };

      in {
        packages = {
          default = self.packages.${system}.search-lambda;
          
          search-lambda = pkgs.rustPlatform.buildRustPackage rec {
            pname = "fdnix-search-lambda";
            version = "0.1.0";

            src = ./.;

            cargoHash = "sha256-z9Oj3YnUmZZvnTc7Ghjjj9VkF4KqRi/2vepkTGOqTtA=";

            inherit nativeBuildInputs buildInputs;
            env = buildEnv;

            # Build for musl target (static linking)
            CARGO_BUILD_TARGET = "x86_64-unknown-linux-musl";

            # Override build phase to ensure proper target
            buildPhase = ''
              runHook preBuild
              
              export CARGO_TARGET_DIR="$NIX_BUILD_TOP/target"
              cargo build --release --target x86_64-unknown-linux-musl --offline
              
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall
              
              mkdir -p $out/bin
              
              # Copy the Lambda bootstrap binary
              cp target/x86_64-unknown-linux-musl/release/bootstrap $out/bin/
              
              # Verify it's statically linked
              echo "Checking binary linkage:"
              file $out/bin/bootstrap
              ldd $out/bin/bootstrap || echo "✓ Static binary - no dynamic dependencies"
              
              runHook postInstall
            '';

            # Skip check phase as we don't need to run tests in the build
            doCheck = false;

            meta = with pkgs.lib; {
              description = "fdnix hybrid search AWS Lambda function with DuckDB";
              license = licenses.mit;
              maintainers = [ ];
              platforms = [ "x86_64-linux" ];
            };
          };

          # Lambda deployment package with CA certificates
          lambda-package = pkgs.stdenv.mkDerivation {
            name = "fdnix-search-lambda-package";
            src = self.packages.${system}.search-lambda;
            
            buildInputs = [ pkgs.zip ];
            
            buildPhase = ''
              mkdir -p lambda-package
              
              # Copy the bootstrap binary
              cp $src/bin/bootstrap lambda-package/
              chmod +x lambda-package/bootstrap
              
              # Copy CA certificates for HTTPS requests
              mkdir -p lambda-package/etc/ssl/certs
              cp ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt lambda-package/etc/ssl/certs/ca-certificates.crt
            '';
            
            installPhase = ''
              mkdir -p $out
              
              # Create the deployment zip
              cd lambda-package
              zip -r $out/lambda-deployment.zip .
              
              # Also provide the raw files
              cp -r . $out/lambda-files/
            '';
          };
        };

        devShells.default = pkgs.mkShell {
          inherit buildInputs nativeBuildInputs;
          env = buildEnv;
          
          shellHook = ''
            echo "fdnix search lambda development environment"
            echo "Rust toolchain: $(rustc --version)"
            echo "Target: x86_64-unknown-linux-musl"
            echo ""
            echo "Available commands:"
            echo "  cargo build --target x86_64-unknown-linux-musl    # Build for Lambda"
            echo "  nix build .#search-lambda                         # Build with Nix"
            echo "  nix build .#lambda-package                        # Build deployment package"
          '';
        };
      });
}