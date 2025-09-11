let
  pkgs = import <nixpkgs> {};
in
[
  # System-level dependencies for nix-eval-jobs
  pkgs.nix-eval-jobs
  pkgs.gcc

  # Minimal Python environment - only what's needed for S3 upload
  (pkgs.python313.withPackages (ps: with ps; [
    boto3
    botocore
    requests
    brotli
  ]))
]
