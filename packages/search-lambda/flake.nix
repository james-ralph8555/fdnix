{
  description = "A statically-compiled Rust AWS Lambda function with DuckDB extensions";

  inputs = {
    # Pinning nixpkgs to a specific commit ensures the build is fully reproducible
    # and not susceptible to changes in nixos-unstable.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };

        # The core solution: custom DuckDB derivation with extensions.
        # This addresses the header bug and statically links the extensions.
        duckdb-with-extensions = pkgs.duckdb.overrideAttrs (oldAttrs: {
          # Use a name specific to this custom build.
          pname = "duckdb-static-extensions";

          # Define the specific extensions to be built statically.
          # The `vss` and `fts` extensions are enabled by these flags.
          cmakeFlags = oldAttrs.cmakeFlags or [] ++ [
            "-DDUCKDB_BUILD_EXTENSIONS=fts;vss"
            "-DDUCKDB_BUILD_ICU_EXTENSION=ON"
            "-DDUCKDB_BUILD_VSS_EXTENSION=ON"
            "-DDUCKDB_BUILD_FTS_EXTENSION=ON"
            "-DDUCKDB_EXTENSION_LINK_STATIC=ON"
            "-DBUILD_SHARED_LIBS=OFF"
            "-DDUCKDB_USE_EXTERNAL_LIBRARIES=ON"
          ];

          # Fix the known header issue by manually adding the `third_party` directory
          # to the C++ include path (`CXXFLAGS`). This is the critical step to resolve
          # the header-not-found errors reported in community forums.
          preConfigure = (oldAttrs.preConfigure or "") + ''
            export CXXFLAGS="$CXXFLAGS -I$src/third_party"
          '';

          # Ensure we have the necessary build dependencies
          nativeBuildInputs = oldAttrs.nativeBuildInputs or [] ++ (with pkgs; [
            cmake
            ninja
            pkg-config
          ]);
        });

        # The final Rust application derivation.
        # This is where all the solutions come together.
        search-lambda = pkgs.rustPlatform.buildRustPackage {
          pname = "fdnix-search-lambda";
          version = "0.1.0";
          src = ./.;

          # The correct Rust target for static compilation.
          # This tells rustc to link against the musl libc.
          target = "x86_64-unknown-linux-musl";

          # The list of dependencies required to build the project.
          # Our custom DuckDB derivation is included here.
          buildInputs = with pkgs; [
            duckdb-with-extensions
          ];

          # Native build tools needed for compilation
          nativeBuildInputs = with pkgs; [
            pkg-config
            cmake
            clang
            llvmPackages.libclang.lib
          ];
          
          # Environment variables for bindgen and rustc stack size
          env = {
            LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
            BINDGEN_EXTRA_CLANG_ARGS = "-I${pkgs.llvmPackages.libclang.lib}/lib/clang/${pkgs.llvmPackages.libclang.version}/include";
            # This resolves the SIGSEGV by increasing rustc's stack size
            # Using a larger stack size to handle complex macro expansions
            RUST_MIN_STACK = "33554432";  # 32MB
          };

          # Use the Cargo.lock file for vendoring dependencies.
          cargoLock = {
            lockFile = ./Cargo.lock;
          };

          # Skip tests during build
          doCheck = false;

          meta = with pkgs.lib; {
            description = "fdnix hybrid search AWS Lambda function with DuckDB";
            license = licenses.mit;
            maintainers = [ ];
            platforms = [ "x86_64-linux" ];
          };
        };

      in {
        packages = {
          default = search-lambda;
          
          # Lambda deployment package with CA certificates
          lambda-package = pkgs.stdenv.mkDerivation {
            name = "fdnix-search-lambda-package";
            src = search-lambda;
            
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
          buildInputs = [ duckdb-with-extensions ];
          nativeBuildInputs = with pkgs; [
            rustc
            cargo
            pkg-config
            cmake
          ];
          
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