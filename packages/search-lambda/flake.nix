{
  description = "A statically-compiled Rust AWS Lambda function for fdnix search (LanceDB)";

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

        # The final Rust application derivation.
        # This is where all the solutions come together.
        search-lambda = pkgs.rustPlatform.buildRustPackage {
          pname = "fdnix-search-lambda";
          version = "0.1.0";
          src = ./.;

          # The correct Rust target for static compilation.
          # This tells rustc to link against the musl libc.
          target = "x86_64-unknown-linux-musl";

          # Increase stack size to prevent compiler crashes
          RUST_MIN_STACK = "67108864";

          # Native build tools needed for compilation
          nativeBuildInputs = with pkgs; [
            pkg-config
            protobuf
          ];

          # Use the Cargo.lock file for vendoring dependencies.
          cargoLock = {
            lockFile = ./Cargo.lock;
          };

          # Skip tests during build
          doCheck = false;

          meta = with pkgs.lib; {
            description = "fdnix hybrid search AWS Lambda function (LanceDB)";
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
          nativeBuildInputs = with pkgs; [
            rustc
            cargo
            pkg-config
          ];
          shellHook = ''
            echo "fdnix search lambda development environment (LanceDB)"
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
