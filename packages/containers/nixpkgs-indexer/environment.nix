let
  pkgs = import <nixpkgs> {};
in
[
  # System-level dependencies
  pkgs.gcc

  # Unified Python environment with its packages
  (pkgs.python313.withPackages (ps: with ps; [
    boto3
    botocore
    requests
    duckdb
    numpy
  ]))
]
