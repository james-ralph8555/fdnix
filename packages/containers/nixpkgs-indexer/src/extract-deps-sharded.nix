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
  # Import lib from the nixpkgs path (treat nixpkgs as a path, not a string)
  lib = import (nixpkgs + "/lib");
  
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
          # For language package sets, they should always recurse even without recurseForDerivations
          shouldRecurse = value.recurseForDerivations or true; # Changed to default true
          children = builtins.removeAttrs value ["recurseForDerivations"];
          childAttrs = lib.attrNames children;
          # No longer limiting children - we want all packages
          limitedAttrs = childAttrs;
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

  # Helper function to create sub-shards from large package sets
  createSubShards = packageSet: prefixChar:
    let
      allAttrs = lib.attrNames packageSet;
      # Filter attributes that start with the prefix character (case-insensitive)
      matchingAttrs = lib.filter 
        (name: 
          let firstChar = lib.toLower (lib.substring 0 1 name);
          in firstChar == lib.toLower prefixChar)
        allAttrs;
      # Create a new attribute set with only matching packages
      subShard = lib.genAttrs matchingAttrs (name: packageSet.${name});
    in subShard;

  # Helper to create alphabetical sub-shards for very large package sets
  createAlphabeticalSubShards = packageSet: ranges:
    lib.listToAttrs (map (range:
      let
        rangeName = builtins.replaceStrings ["-"] ["_"] range;
        startChar = lib.substring 0 1 range;
        endChar = lib.substring 2 1 range;
        allAttrs = lib.attrNames packageSet;
        matchingAttrs = lib.filter 
          (name: 
            let firstChar = lib.toLower (lib.substring 0 1 name);
                startLower = lib.toLower startChar;
                endLower = lib.toLower endChar;
            in firstChar >= startLower && firstChar <= endLower)
          allAttrs;
        subShard = lib.genAttrs matchingAttrs (name: packageSet.${name});
      in {
        name = rangeName;
        value = subShard;
      }) ranges);

  # Dynamic shard discovery - traverse nixpkgs to find all package collections
  discoverAllShards = 
    let
      # Build a comprehensive set of known package collections from nixpkgs
      # This is more reliable than complex recursive discovery
      
      # Core single packages
      corePackages = {
        "stdenv" = pkgs.stdenv or {};
        "coreutils" = pkgs.coreutils or {};  
        "bash" = pkgs.bash or {};
        "gcc" = pkgs.gcc or {};
        "glibc" = pkgs.glibc or {};
        "binutils" = pkgs.binutils or {};
        "findutils" = pkgs.findutils or {};
        "diffutils" = pkgs.diffutils or {};
        "gnused" = pkgs.gnused or {};
        "gawk" = pkgs.gawk or {};
        "gnutar" = pkgs.gnutar or {};
        "gzip" = pkgs.gzip or {};
        "bzip2" = pkgs.bzip2 or {};
        "xz" = pkgs.xz or {};
        "curl" = pkgs.curl or {};
        "wget" = pkgs.wget or {};
        "git" = pkgs.git or {};
      };
      
      # Language-specific package collections
      languagePackages = {
        # Python packages
        "pythonPackages" = pkgs.python3Packages or pkgs.pythonPackages or {};
        "python311Packages" = pkgs.python311Packages or {};
        "python310Packages" = pkgs.python310Packages or {};
        "python39Packages" = pkgs.python39Packages or {};
        "python312Packages" = pkgs.python312Packages or {};
        
        # Node packages
        "nodePackages" = pkgs.nodePackages or {};
        "nodePackages_latest" = pkgs.nodePackages_latest or {};
        
        # Other language packages  
        "perlPackages" = pkgs.perlPackages or {};
        "rubyPackages" = pkgs.rubyPackages or {};
        "phpPackages" = pkgs.php82Packages or {};
        "rPackages" = pkgs.rPackages or {};
        "juliaPackages" = if pkgs ? julia then (pkgs.julia.pkgs or {}) else {};
        "luaPackages" = pkgs.luaPackages or {};
        "ocamlPackages" = pkgs.ocamlPackages or {};
        "rustPackages" = pkgs.rustPackages or {};
        "goPackages" = pkgs.goPackages or {};
      };
      
      # Haskell packages - split alphabetically to avoid stack overflow
      haskellShards = if pkgs ? haskellPackages && pkgs.haskellPackages != {} then
        lib.mapAttrs' (subName: subValue: {
          name = "haskellPackages_${subName}";
          value = subValue;
        }) (createAlphabeticalSubShards pkgs.haskellPackages ["a-d" "e-h" "i-l" "m-p" "q-t" "u-z"])
      else {};
      
      # Desktop environments and window managers
      desktopPackages = {
        "gnome" = pkgs.gnome or {};
        "gnomeExtensions" = pkgs.gnomeExtensions or {};
        "kde" = pkgs.kdePackages or {};
        "plasma5Packages" = pkgs.plasma5Packages or {};
        "xfce" = pkgs.xfce or {};
        "lxqt" = pkgs.lxqt or {};
        "mate" = pkgs.mate or {};
        "pantheon" = pkgs.pantheon or {};
        "enlightenment" = pkgs.enlightenment or {};
        "xorg" = pkgs.xorg or {};
        "wayland" = pkgs.wayland or {};
      };
      
      # System and hardware packages
      systemPackages = {
        "linuxPackages" = pkgs.linuxPackages or {};
        "linuxPackages_latest" = pkgs.linuxPackages_latest or {};
        "firmwareLinuxNonfree" = pkgs.firmwareLinuxNonfree or {};
        "udev" = pkgs.udev or {};
        "systemd" = pkgs.systemd or {};
        "dbus" = pkgs.dbus or {};
        "polkit" = pkgs.polkit or {};
        "networkmanager" = pkgs.networkmanager or {};
        "bluez" = pkgs.bluez or {};
        "pulseaudio" = pkgs.pulseaudio or {};
        "pipewire" = pkgs.pipewire or {};
        "alsa-lib" = pkgs."alsa-lib" or {};
      };
      
      # Development tools and libraries
      developmentPackages = {
        "buildPackages" = pkgs.buildPackages or {};
        "pkgsCross" = pkgs.pkgsCross or {};
        "llvmPackages" = pkgs.llvmPackages or {};
        "llvmPackages_latest" = pkgs.llvmPackages_latest or {};
        "gccStdenv" = pkgs.gccStdenv or {};
        "clangStdenv" = pkgs.clangStdenv or {};
        "cmake" = pkgs.cmake or {};
        "meson" = pkgs.meson or {};
        "ninja" = pkgs.ninja or {};
        "pkgconfig" = pkgs.pkgconfig or {};
        "autoconf" = pkgs.autoconf or {};
        "automake" = pkgs.automake or {};
        "libtool" = pkgs.libtool or {};
      };
      
      # Graphics and multimedia libraries
      graphicsPackages = {
        "gst_all_1" = pkgs.gst_all_1 or {};
        "ffmpeg" = pkgs.ffmpeg or {};
        "mesa" = pkgs.mesa or {};
        "vulkan-loader" = pkgs."vulkan-loader" or {};
        "opengl" = pkgs.opengl or {};
        "xwayland" = pkgs.xwayland or {};
        "cairo" = pkgs.cairo or {};
        "pango" = pkgs.pango or {};
        "gdk-pixbuf" = pkgs."gdk-pixbuf" or {};
        "gtk3" = pkgs.gtk3 or {};
        "gtk4" = pkgs.gtk4 or {};
        "qt5" = pkgs.qt5 or {};
        "qt6" = pkgs.qt6 or {};
      };
      
      # Server software
      serverPackages = {
        "nginx" = pkgs.nginx or {};
        "apache-httpd" = pkgs."apache-httpd" or {};
        "postgresql" = pkgs.postgresql or {};
        "mysql" = pkgs.mysql80 or {};
        "mariadb" = pkgs.mariadb or {};
        "sqlite" = pkgs.sqlite or {};
        "redis" = pkgs.redis or {};
        "mongodb" = pkgs.mongodb or {};
        "elasticsearch" = pkgs.elasticsearch or {};
        "docker" = pkgs.docker or {};
        "podman" = pkgs.podman or {};
      };
      
      # Gaming
      gamingPackages = {
        "steam" = pkgs.steam or {};
        "wine" = pkgs.wine or {};
        "lutris" = pkgs.lutris or {};
        "gamemode" = pkgs.gamemode or {};
      };
      
      # Try to discover top-level categories dynamically as fallback
      discoverTopLevel =
        let
          topLevelNames = lib.attrNames pkgs;
          # Only include true package collections, not individual derivations
          # Heuristics:
          #  - must be an attrset AND NOT a derivation
          #  - should opt-in to recursion (recurseForDerivations = true)
          #  - have a reasonable number of members (avoid tiny helper sets)
          isCollection = value:
            let
              evaluated = builtins.tryEval value;
            in evaluated.success && lib.isAttrs evaluated.value && !lib.isDerivation evaluated.value &&
               (evaluated.value.recurseForDerivations or false) &&
               (lib.length (lib.attrNames (builtins.removeAttrs evaluated.value ["recurseForDerivations"])) >= 20);
          candidateCollections = lib.filter (name: isCollection (pkgs.${name} or {})) topLevelNames;
          discoveredCollections = lib.genAttrs candidateCollections (name: pkgs.${name} or {});
        in discoveredCollections;
      
    in 
    corePackages // 
    languagePackages // 
    haskellShards // 
    desktopPackages // 
    systemPackages // 
    developmentPackages // 
    graphicsPackages // 
    serverPackages // 
    gamingPackages // 
    discoverTopLevel;

  # Generate all available shards dynamically
  allShards = discoverAllShards;

  # Process specific shard or list available shards
  processedData = 
    if shard == null then
      # List available shards
      let
        shardNames = lib.attrNames allShards;
        totalShards = lib.length shardNames;
        # Add debug trace to show discovery results
        discoveryTrace = builtins.trace "[DISCOVERY] Found ${toString totalShards} shards dynamically" true;
      in
      {
        availableShards = shardNames;
        metadata = {
          nixpkgs_version = lib.version or "unknown";
          system = system;
          allow_unfree = allowUnfree;
          allow_aliases = allowAliases;
          total_shards = totalShards;
          discovery_method = "dynamic";
        };
      }
    else if !lib.hasAttr shard allShards then
      # Invalid shard specified
      {
        error = "Invalid shard specified: ${shard}";
        availableShards = lib.attrNames allShards;
      }
    else
      # Process the specified shard
      let
        startTime = builtins.currentTime;
        startTrace = builtins.trace "[SHARD-START] Processing shard: ${shard}" true;
        
        shardAttrSet = allShards.${shard};
        # Debug trace to see if the shard exists and has attributes
        debugTrace = builtins.trace "[SHARD-DEBUG] Shard ${shard} exists: ${toString (shardAttrSet != null)}, isAttrs: ${toString (lib.isAttrs shardAttrSet)}, attrCount: ${toString (lib.length (lib.attrNames shardAttrSet))}" true;
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
          discovery_method = "dynamic";
        };
        packages = uniquePackages;
      };

in processedData
