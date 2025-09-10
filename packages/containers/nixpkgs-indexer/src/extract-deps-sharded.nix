# NixGraph: By-Name Sharded runtime dependency extraction from nixpkgs
# This expression evaluates by-name prefix shards to provide fast, granular
# dependency extraction with optimal parallelization.

{ nixpkgs ? <nixpkgs>
, system ? builtins.currentSystem
, allowUnfree ? false
, shard ? null
, maxDepth ? 8  # Reduced from 10 since by-name packages are typically simpler
, allowAliases ? false
}:

let
  # Import lib from the nixpkgs path
  lib = import (nixpkgs + "/lib");
  
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

  # Safely convert any value to string, falling back to "unknown"
  toStringOrUnknown = v:
    let t = builtins.tryEval (toString v);
        s = if t.success then t.value else "unknown";
    in if s == "" then "unknown" else s;

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

  # Extract dependency information from a derivation
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

  # Extract dependencies from a by-name package
  extractByNamePackage = packageName:
    let
      packageResult = builtins.tryEval (pkgs.${packageName} or null);
    in
    if packageResult.success && packageResult.value != null && lib.isDerivation packageResult.value then
      let extracted = safeExtractDeps packageName packageResult.value;
      in if extracted != null then [extracted] else []
    else [];

  # Define the by-name shard mappings based on our analysis (48 total shards)
  # Large prefix shards (>200 packages each)
  largePrefixShards = {
    "byname_large_re" = ["re"];
    "byname_large_op" = ["op"];
    "byname_large_ma" = ["ma"];
    "byname_large_go" = ["go"];
    "byname_large_ca" = ["ca"];
    "byname_large_co" = ["co"];
    "byname_large_li" = ["li"];
  };

  # Grouped shards (multiple prefixes, targeting ~400 packages each)
  groupedShards = {
    "byname_group_01" = ["_8" "a-" "a4" "a5" "b3" "b6" "bj" "bn" "c6" "cq" "dz" "e-" "ee" "f5" "fj" "fq" "g1" "g2" "g8" "g9" "h8" "hj" "hq" "hv" "hx" "i-" "i8" "ih" "iu" "iz" "j" "j4" "jg" "jl" "jy" "k0" "k4" "k6" "k9" "kf" "kg" "l2" "m-" "m3" "m8" "mw" "n-" "n3" "n8" "nj" "p0" "p1" "p2" "p3" "p9" "pj" "pz" "q-" "q2" "ql" "qz" "r0" "r5" "s0" "s4" "s7" "s9" "sj" "t1" "t3" "t4" "tq" "u0" "u2" "u3" "u9" "uk" "vf" "vx" "vz" "w3" "ww" "x-" "x3" "x8" "yc" "yj" "yl" "yn" "yp" "yq" "ys" "yx" "yy" "z-" "z8" "zy" "_6" "a2" "aj" "aq" "b4" "bq" "bv" "bz" "d-" "d2" "dq" "e2" "ew" "ey" "f1" "f3" "fh" "g-" "g3" "gk" "h2" "h5" "hb" "hl" "hn" "hr" "ie" "ik" "iq" "k2" "k3" "k8" "kx" "lf" "lg" "lh" "mh" "n9" "nk" "oe" "oq" "p4" "qh" "qi" "qw" "r1" "r2" "rg" "rr" "rv" "rz" "s-" "s5" "sz" "t-" "uq" "ux" "v4" "vv" "vy" "w_" "wk" "wq" "wu" "xy" "xz" "ym" "z3" "zb" "zg" "zn" "zv" "zw" "zz" "_2" "_4" "_7" "ao" "bb" "c3" "cz" "dg" "dk" "e1" "eo" "f2" "fg" "fy" "gj" "gy" "hh" "ib" "ij" "jc" "jh" "jj" "jx" "kv" "kw" "m1" "m2" "m4" "mx" "n2" "nq" "nz" "px" "qe" "qg" "qq" "rf" "rx" "s2" "u-" "vb" "vh" "vw" "wb" "xg" "xj" "xu" "yd" "zc" "zt" "_3"];
    "byname_group_02" = ["ak" "ck" "cx" "dx" "ek" "fv" "gw" "gz" "hf" "hm" "hw" "i2" "il" "jr" "jt" "kp" "lk" "lw" "mg" "mj" "nh" "nx" "pv" "qa" "qc" "qs" "rq" "sx" "tg" "tv" "tz" "vn" "wf" "x4" "xh" "xq" "yg" "zk" "zm" "zr" "_0" "_1" "_9" "ah" "ax" "bg" "bk" "bm" "bw" "by" "c2" "cj" "dl" "ej" "eq" "fx" "gg" "gq" "ii" "jd" "jm" "jn" "mv" "pq" "qb" "qp" "qt" "rw" "tb" "tk" "tx" "uv" "v2" "wd" "wv" "wy" "x1" "xv" "xx" "zd" "bd" "c-" "fm" "fw" "fz" "gb"];
    "byname_group_03" = ["hs" "iw" "jw" "ky" "lb" "lp" "mn" "nl" "nn" "ny" "og" "oh" "oi" "ok" "qd" "rb" "rl" "sg" "tn" "ua" "uf" "ui" "uu" "vg" "vl" "vr" "x2" "zl" "eu" "ez" "fd" "fn" "ft" "gv" "iv" "jq" "kh" "lr" "lv" "oo" "qo" "s3" "vd" "wc" "wt" "xw" "zu" "zx" "bf" "cw" "dh" "eb" "gx" "i3" "kb" "of" "pb" "qm" "rk"];
    "byname_group_04" = ["td" "ue" "ul" "xn" "zp" "cv" "dp" "dv" "dw" "hc" "hp" "ig" "jb" "jf" "kl" "km" "ks" "kt" "ll" "ln" "ly" "mq" "nm" "ol" "ou" "pp" "rh" "ry" "uh" "uw" "vp" "vt" "wp" "wx" "aa" "cn" "dj" "ef" "ei" "ia" "lm" "lz" "mr"];
    "byname_group_05" = ["nd" "nf" "ox" "qr" "rn" "ug" "um" "wr" "xk" "zf" "ay" "df" "eg" "fp" "kd" "mb" "mt" "nr" "pt" "ub" "vk" "vm" "ws" "dt" "dy" "if" "jp" "kc" "lt" "np" "oa" "om" "ow" "pf" "pm" "rc"];
    "byname_group_06" = ["rd" "rm" "tf" "ts" "ut" "vs" "wg" "wm" "xb" "af" "bc" "bp" "cg" "fb" "ov" "pk" "sf" "xf" "xi" "xl" "xo" "xr" "xt" "cb" "ct" "gm" "lc" "lx" "nb" "ng"];
    "byname_group_07" = ["pw" "sb" "vc" "yu" "az" "id" "it" "nw" "ye" "yt" "za" "cf" "dm" "ir" "ld" "ml" "nt" "od" "ot" "pn" "rp" "tc" "tl" "uc" "zo"];
    "byname_group_08" = ["bs" "ds" "gc" "hd" "mk" "ns" "xp" "yo" "ae" "cy" "dc" "gd" "kn" "nc" "ob" "xa" "cc" "er" "ff" "ls" "nv" "tm"];
    "byname_group_09" = ["xe" "bt" "cd" "dd" "es" "et" "av" "cm" "hu" "is" "kr" "tp" "ud" "ur" "vu" "je" "ji" "mc" "mf"];
    "byname_group_10" = ["tw" "ab" "fc" "gs" "io" "sv" "xs" "ag" "db" "ep" "gf" "mm" "rt" "sk" "sr" "ec"];
    "byname_group_11" = ["fs" "ty" "up" "ai" "cs" "ea" "wh" "xd" "ic" "js" "or" "pc" "us" "vo"];
    "byname_group_12" = ["ev" "my" "ed" "fu" "ju" "ps" "rs" "ac" "jo" "on" "ph" "ms" "oc"];
    "byname_group_13" = ["xc" "dn" "em" "xm" "zi" "tt" "fe" "pu" "at" "ce" "dr"];
    "byname_group_14" = ["ki" "sd" "ve" "wl" "hi" "ip" "sq" "pg" "ze" "nu"];
    "byname_group_15" = ["pd" "wo" "lu" "el" "gp" "py" "sl" "cp" "gh"];
    "byname_group_16" = ["ja" "ko" "va" "du" "mp" "th" "am" "ci" "aw"];
    "byname_group_17" = ["gt" "pe" "zs" "im" "ad" "ho" "md" "we"];
    "byname_group_18" = ["sm" "tu" "ht" "os" "qu" "en" "ex"];
    "byname_group_19" = ["ss" "br" "ri" "cu" "be" "ga" "ke"];
    "byname_group_20" = ["ya" "gl" "fo" "mu" "gu" "na"];
    "byname_group_21" = ["pl" "hy" "sn" "vi" "as"];
    "byname_group_22" = ["au" "bu" "ka" "wi" "da"];
    "byname_group_23" = ["lo" "sw" "sy" "ap" "la"];
    "byname_group_24" = ["le" "sa" "bl" "to" "an"];
    "byname_group_25" = ["bi" "fr" "ru" "su" "ge"];
    "byname_group_26" = ["he" "bo" "ku" "cr"];
    "byname_group_27" = ["ro" "ti" "no" "ar"];
    "byname_group_28" = ["ha" "ra" "se" "di"];
    "byname_group_29" = ["sh" "un" "si" "fi"];
    "byname_group_30" = ["fl" "tr" "al"];
    "byname_group_31" = ["fa" "gr" "sp"];
    "byname_group_32" = ["in" "de" "ba"];
    "byname_group_33" = ["sc" "so" "ta"];
    "byname_group_34" = ["pi" "wa" "do"];
    "byname_group_35" = ["me" "ch" "ni"];
    "byname_group_36" = ["gn" "cl"];
    "byname_group_37" = ["po" "mi"];
    "byname_group_38" = ["ne" "mo"];
    "byname_group_39" = ["st" "te"];
    "byname_group_40" = ["pa" "pr"];
    "byname_group_41" = ["gi"];
  };

  # Combine all shard definitions
  allShardDefinitions = largePrefixShards // groupedShards;

  # Helper to get actual package names from by-name directory structure
  getByNamePackagesForPrefix = prefix:
    let
      # Try to read the by-name directory for this prefix
      byNameDir = builtins.readDir (nixpkgs + "/pkgs/by-name/${prefix}");
      packageNames = lib.attrNames byNameDir;
    in packageNames;

  # Generate packages for a specific shard by extracting by-name packages
  generateShardPackages = shardName: prefixes:
    let
      # For each prefix, get the actual package names from the by-name directory
      getPackagesForPrefix = prefix:
        let
          packageNames = builtins.tryEval (getByNamePackagesForPrefix prefix);
          validPackageNames = if packageNames.success then packageNames.value else [];
        in lib.concatMap extractByNamePackage validPackageNames;
      
      # Get packages for all prefixes in this shard
      allPackagesInShard = lib.concatMap getPackagesForPrefix prefixes;
      validPackages = lib.filter (pkg: pkg != null && pkg.pname != null) allPackagesInShard;
      
      # Remove duplicates within the shard
      uniquePackages = lib.foldl' 
        (acc: pkg: 
          if lib.any (p: p.id == pkg.id) acc 
          then acc 
          else acc ++ [pkg])
        []
        validPackages;
    in uniquePackages;

  # Process specific shard or list available shards
  processedData = 
    if shard == null then
      # List available shards
      let
        shardNames = lib.attrNames allShardDefinitions;
        totalShards = lib.length shardNames;
      in
      {
        availableShards = shardNames;
        metadata = {
          nixpkgs_version = lib.version or "unknown";
          system = system;
          allow_unfree = allowUnfree;
          allow_aliases = allowAliases;
          total_shards = totalShards;
          sharding_strategy = "by_name_prefixes";
          max_depth = maxDepth;
        };
      }
    else if !lib.hasAttr shard allShardDefinitions then
      # Invalid shard specified
      {
        error = "Invalid shard specified: ${shard}";
        availableShards = lib.attrNames allShardDefinitions;
        sharding_strategy = "by_name_prefixes";
      }
    else
      # Process the specified shard
      let
        startTime = builtins.currentTime;
        shardPrefixes = allShardDefinitions.${shard};
        packages = generateShardPackages shard shardPrefixes;
        
        endTime = builtins.currentTime;
        duration = endTime - startTime;
      in
      {
        metadata = {
          nixpkgs_version = lib.version or "unknown";
          extraction_timestamp = toString startTime;
          shard_name = shard;
          shard_prefixes = shardPrefixes;
          shard_duration_seconds = duration;
          total_packages = lib.length packages;
          system = system;
          allow_unfree = allowUnfree;
          allow_aliases = allowAliases;
          max_depth = maxDepth;
          sharding_strategy = "by_name_prefixes";
        };
        packages = packages;
      };

in processedData