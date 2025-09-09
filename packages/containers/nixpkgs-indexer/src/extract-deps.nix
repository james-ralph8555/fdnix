# NixGraph: Extract runtime dependencies from nixpkgs
# This expression evaluates all packages in nixpkgs and extracts their runtime dependencies
# without building anything, outputting structured JSON data.

{ nixpkgs ? <nixpkgs>
, system ? builtins.currentSystem
, allowUnfree ? false
}:

let
  lib = import "${nixpkgs}/lib";
  
  # Safely convert any value to string, falling back to "unknown"
  toStringOrUnknown = v:
    let t = builtins.tryEval (toString v);
        s = if t.success then t.value else "unknown";
    in if s == "" then "unknown" else s;
  
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
      
      # Ensure version is always a usable string (handle nulls/non-strings)
      version = toStringOrUnknown (tryGetAttr "version" "unknown");
      
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

  # Progress tracking - log every N packages processed
  # Nix doesn't have modulo operator, so we use division and multiplication to check divisibility
  isDivisibleBy = n: divisor: (n / divisor) * divisor == n;
  logProgress = count: msg: 
    if count > 0 && (count == 1 || count == 100 || count == 500 || count == 1000 || (count >= 2000 && isDivisibleBy count 2000))
    then builtins.trace "[PROGRESS] ${toString count} packages processed: ${msg}" true
    else true;

  # Collect ALL derivations recursively, respecting recurseForDerivations
  collectAllDerivations = pkgSet:
    let
      # Start by marking the root set to recurse into
      root = lib.recurseIntoAttrs pkgSet;

      # Recursive traversal with cycle detection; respects recurseForDerivations
      go = visited: path: value: count:
        let
          attrPath = lib.concatStringsSep "." path;
          newCount = count + 1;
          # Log progress periodically
          progressTrace = logProgress newCount attrPath;
        in
        if lib.elem attrPath visited then { result = []; count = newCount; } else
        let
          tried = builtins.tryEval value;
          newVisited = visited ++ [attrPath];
        in if !tried.success then { result = []; count = newCount; }
        else
          let 
            v = tried.value; 
            currentCount = newCount;
          in
          if lib.isDerivation v then
            let 
              extracted = safeExtractDeps attrPath v;
              result = if extracted != null then [ extracted ] else [];
            in { inherit result; count = currentCount; }
          else if lib.isAttrs v then
            # Only recurse into attribute sets that opt-in, except the root which we already marked
            let
              shouldRecurse = v.recurseForDerivations or false;
              # Avoid carrying the marker attribute into children
              children = builtins.removeAttrs v [ "recurseForDerivations" ];
            in if shouldRecurse then
              let
                childResults = lib.mapAttrsToList (n: val: go newVisited (path ++ [ n ]) val currentCount) children;
                finalResults = map (r: r.result) childResults;
                finalCount = lib.foldl' lib.max currentCount (map (r: r.count) childResults);
              in { result = lib.concatLists finalResults; count = finalCount; }
            else { result = []; count = currentCount; }
          else if lib.isList v then
            let
              listResults = map (idxVal: go newVisited path idxVal currentCount) v;
              finalResults = map (r: r.result) listResults;
              finalCount = lib.foldl' lib.max currentCount (map (r: r.count) listResults);
            in { result = lib.concatMap (x: x) finalResults; count = finalCount; }
          else { result = []; count = currentCount; };
    in
    # Traverse from the root; we don't pre-filter top-level attrs and we don't cap
    let 
      startTime = builtins.currentTime;
      startTrace = builtins.trace "[START] Beginning nixpkgs dependency extraction..." true;
      result = go [] [] root 0;
      endTime = builtins.currentTime;
      duration = endTime - startTime;
      endTrace = builtins.trace "[COMPLETE] Extraction finished. Duration: ${toString duration}s, Total packages: ${toString (lib.length result.result)}" true;
    in result.result;

  # Main extraction logic with memory safeguards
  allPackages = collectAllDerivations pkgs;

  # Filter out null/empty results and duplicates
  validPackages = lib.filter (pkg: pkg != null && pkg.pname != null) allPackages;
  
  # Memory safeguard: If we have too many packages (>100k), log a warning and continue
  # This helps prevent OOM in containers with limited memory
  packageCount = lib.length validPackages;
  memoryTrace = if packageCount > 100000 
      then builtins.trace "[WARNING] Processing ${toString packageCount} packages - this may consume significant memory" true
      else if packageCount > 50000
      then builtins.trace "[INFO] Processing ${toString packageCount} packages" true  
      else true;
  
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
