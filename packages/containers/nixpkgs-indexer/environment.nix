let
  pkgs = import <nixpkgs> {};
in
[
  # System-level dependencies
  pkgs.gcc
  pkgs.rustc
  pkgs.cargo

  # Unified Python environment with its packages
  (pkgs.python313.withPackages (ps: with ps; [
    boto3
    botocore
    requests
    httpx
    numpy
    pydantic
    pandas
    pyarrow
    lancedb
    pip
  ]))
]
