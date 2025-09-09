{
  description = "NixGraph: Nixpkgs Runtime Dependency Tree Extraction";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        
        # Python environment with required packages
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          # No additional Python packages needed currently
          # Could add networkx, pandas, etc. for future analysis features
        ]);

        # Development shell with all required tools
        devShell = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Core Nix tools
            nix
            
            # JSON processing
            jq
            
            # Python for data processing
            pythonEnv
            
            # Development tools
            git
            
            # Optional: tools for analysis and visualization
            # Commented out to keep minimal by default
            # gnuplot
            # graphviz
          ];

          shellHook = ''
            echo "🔧 NixGraph Development Environment"
            echo "=================================="
            echo ""
            echo "Available tools:"
            echo "  • nix-instantiate - for Nix evaluation"
            echo "  • python3 - for data processing"
            echo "  • jq - for JSON manipulation"
            echo ""
            echo "Quick start:"
            echo "  ./extract-dependencies.sh --test"
            echo ""
            echo "Documentation:"
            echo "  cat README.md"
            echo ""
          '';
        };

        # Package for the extraction tool itself
        nixgraph = pkgs.stdenv.mkDerivation {
          pname = "nixgraph";
          version = "1.0.0";
          
          src = ./.;
          
          buildInputs = [ pkgs.bash pythonEnv ];
          
          installPhase = ''
            mkdir -p $out/bin $out/share/nixgraph
            
            # Install main script
            cp extract-dependencies.sh $out/bin/nixgraph-extract
            chmod +x $out/bin/nixgraph-extract
            
            # Install Nix expression
            cp extract-deps.nix $out/share/nixgraph/
            
            # Install processing scripts
            mkdir -p $out/share/nixgraph/scripts
            cp scripts/process-deps.py $out/share/nixgraph/scripts/
            chmod +x $out/share/nixgraph/scripts/process-deps.py
            
            # Patch script paths
            substituteInPlace $out/bin/nixgraph-extract \
              --replace "SCRIPT_DIR=\"\$(cd \"\$(dirname \"\''${BASH_SOURCE[0]}\")" && pwd)\"" \
                        "SCRIPT_DIR=\"$out/share/nixgraph\""
          '';
          
          meta = with pkgs.lib; {
            description = "Extract runtime dependency trees from Nixpkgs without building";
            longDescription = ''
              NixGraph is a tool for extracting the complete runtime dependency tree
              from Nixpkgs packages without building them. It uses Nix's evaluation
              capabilities to gather dependency metadata and outputs structured JSON
              for analysis.
            '';
            homepage = "https://github.com/your-org/nixgraph"; # Update with actual URL
            license = licenses.mit; # Update with actual license
            maintainers = [ ]; # Add maintainers
            platforms = platforms.unix;
            mainProgram = "nixgraph-extract";
          };
        };

        # Script to extract from latest nixpkgs
        extractLatest = pkgs.writeShellScriptBin "nixgraph-extract-latest" ''
          set -euo pipefail
          
          echo "🔄 Extracting dependencies from latest nixpkgs..."
          
          # Create a temporary directory
          TEMP_DIR=$(mktemp -d)
          trap "rm -rf $TEMP_DIR" EXIT
          
          # Clone latest nixpkgs
          echo "📦 Cloning latest nixpkgs..."
          git clone --depth 1 https://github.com/NixOS/nixpkgs.git "$TEMP_DIR/nixpkgs"
          
          # Extract dependencies
          echo "⚡ Running extraction..."
          ${nixgraph}/bin/nixgraph-extract \
            --nixpkgs "$TEMP_DIR/nixpkgs" \
            --output "./output-latest" \
            "$@"
          
          echo "✅ Latest nixpkgs extraction complete!"
        '';

      in
      {
        # Development shell
        devShells.default = devShell;
        
        # Packages
        packages = {
          default = nixgraph;
          nixgraph = nixgraph;
          extract-latest = extractLatest;
        };
        
        # Apps that can be run with `nix run`
        apps = {
          default = flake-utils.lib.mkApp {
            drv = nixgraph;
            exePath = "/bin/nixgraph-extract";
          };
          
          extract = flake-utils.lib.mkApp {
            drv = nixgraph;
            exePath = "/bin/nixgraph-extract";
          };
          
          extract-latest = flake-utils.lib.mkApp {
            drv = extractLatest;
            exePath = "/bin/nixgraph-extract-latest";
          };
          
          process = flake-utils.lib.mkApp {
            drv = pkgs.writeShellScript "nixgraph-process" ''
              exec ${pythonEnv}/bin/python3 ${nixgraph}/share/nixgraph/scripts/process-deps.py "$@"
            '';
          };
        };

        # Checks for CI/testing
        checks = {
          # Test script syntax
          bash-syntax = pkgs.runCommand "bash-syntax-check" {} ''
            ${pkgs.bash}/bin/bash -n ${./extract-dependencies.sh}
            touch $out
          '';
          
          # Test Python syntax
          python-syntax = pkgs.runCommand "python-syntax-check" {} ''
            ${pythonEnv}/bin/python3 -m py_compile ${./scripts/process-deps.py}
            touch $out
          '';
          
          # Test Nix expression syntax
          nix-syntax = pkgs.runCommand "nix-syntax-check" {} ''
            ${pkgs.nix}/bin/nix-instantiate --parse ${./extract-deps.nix} > /dev/null
            touch $out
          '';
        };
      }
    );
}