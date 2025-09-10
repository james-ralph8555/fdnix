let
  pkgs = import <nixpkgs> {};
in
[
  # System-level dependencies for data processing
  pkgs.gcc
  pkgs.rustc
  pkgs.cargo

  # Full Python environment with data processing packages
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
    pylance
    pip
  ]))
]