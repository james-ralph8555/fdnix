# NixGraph: Sharded runtime dependency extraction from nixpkgs
# This expression evaluates specific shards of nixpkgs to avoid stack overflows
# while still extracting comprehensive runtime dependency information.

{ nixpkgs ? <nixpkgs>
, system ? builtins.currentSystem
, allowUnfree ? false
, shard ? null
, maxDepth ? 10
, allowAliases ? false
}:

let
  lib = import "${nixpkgs}/lib";
  
  # Safely convert any value to string, falling back to "unknown"
  toStringOrUnknown = v:
    let t = builtins.tryEval (toString v);
        s = if t.success then t.value else "unknown";
    in if s == "" then "unknown" else s;
  
  # Import nixpkgs with configuration optimized for metadata extraction
  pkgs = import nixpkgs {
    inherit system;
    config = {
      inherit allowUnfree allowAliases;
      # Disable all checks and builds - we only want metadata
      doCheck = false;
      dontBuild = true;
      # Reduce evaluation overhead
      checkMeta = false;
    };
  };

  # Helper to extract store path name safely
  extractStoreName = path: 
    let str = toString path;
    in lib.last (lib.splitString "/" str);

  # Helper to convert store paths to package names with comprehensive error handling
  storePaths2Names = paths:
    let
      safeExtractStoreName = path:
        let result = builtins.tryEval (extractStoreName path);
        in if result.success then result.value else null;
      
      processedPaths = 
        if lib.isList paths then paths
        else if lib.isString paths then lib.filter (p: p != "") (lib.splitString " " paths)
        else [];
      
      extractedNames = map safeExtractStoreName processedPaths;
      validNames = lib.filter (name: name != null && name != "") extractedNames;
    in validNames;

  # Extract dependency information with enhanced safety
  extractDepsFromDrv = name: drv:
    let
      tryGetAttr = attr: default: 
        let result = builtins.tryEval (
              if builtins.hasAttr attr drv then drv.${attr} else default
            );
        in if result.success then result.value else default;
      
      safeGetList = attr: 
        let value = tryGetAttr attr [];
        in if lib.isList value then value 
           else if lib.isString value then lib.splitString " " value
           else [];
      
      buildInputs = safeGetList "buildInputs";
      propagatedBuildInputs = safeGetList "propagatedBuildInputs";
      
      pname = let
        directPname = tryGetAttr "pname" null;
        fallbackName = tryGetAttr "name" name;
        extractedPname = if directPname == null && lib.isString fallbackName
                        then lib.head (lib.splitString "-" fallbackName)
                        else directPname;
      in if extractedPname != null then extractedPname else "unknown";
      
      version = toStringOrUnknown (tryGetAttr "version" "unknown");
      isValidPname = lib.isString pname && pname != "" && pname != "unknown";
      
    in if !isValidPname then null else {
      id = "${pname}-${version}";
      inherit pname version;
      buildInputs = lib.filter (dep: dep != null && dep != "") 
                     (storePaths2Names buildInputs);
      propagatedBuildInputs = lib.filter (dep: dep != null && dep != "") 
                               (storePaths2Names propagatedBuildInputs);
      attrPath = name;
      shard = shard;
    };

  # Safe extraction wrapper
  safeExtractDeps = name: drv:
    let result = builtins.tryEval (extractDepsFromDrv name drv);
    in if result.success then result.value else null;

  # Cycle detection for safer traversal
  hasCycle = visited: path: lib.elem path visited;

  # Bounded recursive collection with cycle detection
  collectDerivationsInShard = attrSet: attrPath: depth: visited:
    let
      newVisited = visited ++ [attrPath];
      result = builtins.tryEval attrSet;
    in
    if depth <= 0 || !result.success || hasCycle visited attrPath then []
    else
      let
        value = result.value;
        newDepth = depth - 1;
      in
      if lib.isDerivation value then
        let extracted = safeExtractDeps attrPath value;
        in if extracted != null then [extracted] else []
      else if lib.isAttrs value then
        let
          shouldRecurse = value.recurseForDerivations or false;
          children = builtins.removeAttrs value ["recurseForDerivations"];
          childAttrs = lib.attrNames children;
          # Limit number of children to prevent explosion
          limitedAttrs = if lib.length childAttrs > 1000 
                        then lib.take 1000 childAttrs 
                        else childAttrs;
        in
        if shouldRecurse then
          lib.concatMap 
            (name: 
              let childPath = if attrPath == "" then name else "${attrPath}.${name}";
                  childResult = builtins.tryEval (children.${name});
              in if childResult.success 
                 then collectDerivationsInShard childResult.value childPath newDepth newVisited
                 else [])
            limitedAttrs
        else []
      else [];

  # Define known shards to process
  knownShards = {
    # Core packages - usually safe and fast
    "stdenv" = pkgs.stdenv or {};
    "coreutils" = pkgs.coreutils or {};
    "bash" = pkgs.bash or {};
    "gcc" = pkgs.gcc or {};
    
    # Language ecosystems - these are the problematic ones we're sharding
    "pythonPackages" = pkgs.python3Packages or {};
    "python311Packages" = pkgs.python311Packages or {};
    "python310Packages" = pkgs.python310Packages or {};
    "python39Packages" = pkgs.python39Packages or {};
    
    "haskellPackages" = pkgs.haskellPackages or {};
    "haskell" = pkgs.haskell.packages.ghc94 or {};
    
    "nodePackages" = pkgs.nodePackages or {};
    "nodePackages_latest" = pkgs.nodePackages_latest or {};
    
    "perlPackages" = pkgs.perlPackages or {};
    "rubyPackages" = pkgs.rubyPackages or {};
    "phpPackages" = pkgs.php82Packages or {};
    
    "rPackages" = pkgs.rPackages or {};
    "juliaPackages" = pkgs.julia.pkgs or {};
    
    # Development tools
    "gitAndTools" = pkgs.gitAndTools or {};
    "linuxPackages" = pkgs.linuxPackages or {};
    
    # Desktop environments
    "gnome" = pkgs.gnome or {};
    "kde" = pkgs.kdePackages or {};
    "xorg" = pkgs.xorg or {};
    
    # Servers and databases
    "postgresql" = pkgs.postgresql or {};
    "mysql" = pkgs.mysql80 or {};
    "nginx" = pkgs.nginx or {};
    
    # System libraries
    "gst_all_1" = pkgs.gst_all_1 or {};
    "llvmPackages" = pkgs.llvmPackages or {};
    "qt6" = pkgs.qt6 or {};
    "gtk3" = pkgs.gtk3 or {};
  };

  # Process specific shard or list available shards
  processedData = 
    if shard == null then
      # List available shards
      {
        availableShards = lib.attrNames knownShards;
        metadata = {
          nixpkgs_version = lib.version or "unknown";
          system = system;
          allow_unfree = allowUnfree;
          allow_aliases = allowAliases;
          total_shards = lib.length (lib.attrNames knownShards);
        };
      }
    else if !lib.hasAttr shard knownShards then
      # Invalid shard specified
      {
        error = "Invalid shard specified: ${shard}";
        availableShards = lib.attrNames knownShards;
      }
    else
      # Process the specified shard
      let
        startTime = builtins.currentTime;
        startTrace = builtins.trace "[SHARD-START] Processing shard: ${shard}" true;
        
        shardAttrSet = knownShards.${shard};
        packages = collectDerivationsInShard shardAttrSet shard maxDepth [];
        validPackages = lib.filter (pkg: pkg != null && pkg.pname != null) packages;
        
        # Remove duplicates within the shard
        uniquePackages = lib.foldl' 
          (acc: pkg: 
            if lib.any (p: p.id == pkg.id) acc 
            then acc 
            else acc ++ [pkg])
          []
          validPackages;
        
        endTime = builtins.currentTime;
        duration = endTime - startTime;
        endTrace = builtins.trace "[SHARD-COMPLETE] Shard ${shard} processed in ${toString duration}s: ${toString (lib.length uniquePackages)} packages" true;
      in
      {
        metadata = {
          nixpkgs_version = lib.version or "unknown";
          extraction_timestamp = toString startTime;
          shard_name = shard;
          shard_duration_seconds = duration;
          total_packages = lib.length uniquePackages;
          system = system;
          allow_unfree = allowUnfree;
          allow_aliases = allowAliases;
          max_depth = maxDepth;
        };
        packages = uniquePackages;
      };

in processedData