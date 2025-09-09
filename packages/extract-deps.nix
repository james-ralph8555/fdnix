# NixGraph: Extract runtime dependencies from nixpkgs
# This expression evaluates all packages in nixpkgs and extracts their runtime dependencies
# without building anything, outputting structured JSON data.

{ nixpkgs ? <nixpkgs>
, system ? builtins.currentSystem
, allowUnfree ? false
}:

let
  lib = import "${nixpkgs}/lib";
  
  # Import nixpkgs with configuration
  pkgs = import nixpkgs {
    inherit system;
    config = {
      inherit allowUnfree;
      # Disable all checks and builds - we only want metadata
      doCheck = false;
      dontBuild = true;
    };
  };

  # Helper to extract store path name (e.g., "/nix/store/abc123-hello-1.0" -> "hello-1.0")
  extractStoreName = path: 
    let str = toString path;
    in lib.last (lib.splitString "/" str);

  # Helper to convert a list of store paths to package names with error handling
  storePaths2Names = paths:
    let
      # Safely extract store name with error handling
      safeExtractStoreName = path:
        let 
          result = builtins.tryEval (extractStoreName path);
        in if result.success then result.value else null;
      
      # Process different input types
      processedPaths = 
        if lib.isList paths then
          paths
        else if lib.isString paths then
          # Handle space-separated string of paths, filter out empty strings
          lib.filter (p: p != "") (lib.splitString " " paths)
        else [];
      
      # Extract names and filter out nulls/empty strings
      extractedNames = map safeExtractStoreName processedPaths;
      validNames = lib.filter (name: name != null && name != "") extractedNames;
    in
    validNames;

  # Extract dependency information from a derivation with enhanced error handling
  extractDepsFromDrv = name: drv:
    let
      # Safely try to evaluate attributes with more robust error handling
      tryGetAttr = attr: default: 
        let 
          result = builtins.tryEval (
            if builtins.hasAttr attr drv then drv.${attr} else default
          );
        in if result.success then result.value else default;
      
      # More robust attribute extraction
      safeGetList = attr: 
        let 
          value = tryGetAttr attr [];
        in if lib.isList value then value 
           else if lib.isString value then lib.splitString " " value
           else [];
      
      # Get runtime dependencies with safer extraction
      buildInputs = safeGetList "buildInputs";
      propagatedBuildInputs = safeGetList "propagatedBuildInputs";
      
      # Get package metadata with fallbacks
      pname = let
        directPname = tryGetAttr "pname" null;
        fallbackName = tryGetAttr "name" name;
        # Extract pname from name if it follows pattern "pname-version"  
        extractedPname = if directPname == null && lib.isString fallbackName
                        then lib.head (lib.splitString "-" fallbackName)
                        else directPname;
      in if extractedPname != null then extractedPname else "unknown";
      
      version = tryGetAttr "version" "unknown";
      
      # Validate extracted data
      isValidPname = lib.isString pname && pname != "" && pname != "unknown";
      
    in if !isValidPname then null else {
      # Create a unique identifier
      id = "${pname}-${version}";
      inherit pname version;
      
      # Convert dependency paths to names with error handling
      buildInputs = lib.filter (dep: dep != null && dep != "") 
                     (storePaths2Names buildInputs);
      propagatedBuildInputs = lib.filter (dep: dep != null && dep != "") 
                               (storePaths2Names propagatedBuildInputs);
      
      # Store original attribute path for reference
      attrPath = name;
      
      # Add metadata for debugging
      meta = {
        hasValidPname = isValidPname;
        originalName = tryGetAttr "name" null;
      };
    };

  # Extract dependencies from a derivation, handling evaluation errors
  safeExtractDeps = name: drv:
    let
      result = builtins.tryEval (extractDepsFromDrv name drv);
    in
      if result.success then result.value else null;

  # Safely collect derivations from the package set with cycle detection
  collectAllDerivations = pkgSet:
    let
      # Known problematic attribute patterns to skip
      skipPatterns = [
        "recurseForDerivations"
        "override"
        "overrideDerivation" 
        "__functor"
        "_module"
        "passthru"
      ];
      
      # Check if an attribute name should be skipped
      shouldSkip = name: lib.any (pattern: lib.hasInfix pattern name) skipPatterns;
      
      # Safe traversal with cycle detection and conservative limits
      safeTraverse = visited: path: value:
        let
          attrPath = lib.concatStringsSep "." path;
          pathKey = attrPath;
        in
        # Check for cycles
        if lib.elem pathKey visited then
          []
        # Limit depth more conservatively 
        else if (lib.length path) >= 2 then
          []
        else
          let
            result = builtins.tryEval value;
            newVisited = visited ++ [pathKey];
          in
          if !result.success then
            []
          else if lib.isDerivation result.value then
            # Found a derivation - extract its dependencies
            let extracted = safeExtractDeps attrPath result.value;
            in if extracted != null then [extracted] else []
          else if lib.isAttrs result.value && !lib.isDerivation result.value then
            # Only recurse into attribute sets if they look safe
            let
              # Filter out problematic attributes
              safeAttrs = lib.filterAttrs (name: val: 
                !shouldSkip name && 
                name != "recurseForDerivations" &&
                !(lib.hasPrefix "__" name)
              ) result.value;
              
              # Limit the number of attributes we process per level
              limitedAttrs = lib.listToAttrs (lib.take 50 (lib.attrsToList safeAttrs));
            in
            lib.concatLists (lib.mapAttrsToList 
              (name: val: safeTraverse newVisited (path ++ [name]) val) 
              limitedAttrs)
          else
            [];
      
      # Process top-level attributes with careful filtering
      topLevelAttrs = lib.filterAttrs (name: val:
        !shouldSkip name &&
        name != "recurseForDerivations" &&
        !(lib.hasPrefix "__" name) &&
        # Skip some known large/problematic top-level sets
        !(lib.elem name ["lib" "config" "overlays" "pkgsCross"])
      ) pkgSet;
      
    in
    lib.concatLists (lib.mapAttrsToList 
      (name: val: safeTraverse [] [name] val) 
      topLevelAttrs);

  # Main extraction logic
  allPackages = collectAllDerivations pkgs;

  # Filter out null/empty results and duplicates
  validPackages = lib.filter (pkg: pkg != null && pkg.pname != null) allPackages;
  
  # Remove duplicates based on id
  uniquePackages = lib.foldl' 
    (acc: pkg: 
      if lib.any (p: p.id == pkg.id) acc 
      then acc 
      else acc ++ [pkg])
    []
    validPackages;

  # Get nixpkgs metadata
  nixpkgsInfo = builtins.tryEval (lib.version or "unknown");
  nixpkgsVersion = if nixpkgsInfo.success then nixpkgsInfo.value else "unknown";

  # Current timestamp (approximation)
  timestamp = toString builtins.currentTime;

in
{
  # Metadata about the extraction
  metadata = {
    nixpkgs_version = nixpkgsVersion;
    extraction_timestamp = timestamp;
    total_packages = lib.length uniquePackages;
    system = system;
    allow_unfree = allowUnfree;
  };
  
  # The actual package dependency data
  packages = uniquePackages;
}