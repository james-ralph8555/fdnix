use std::env;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    
    // Check if we're building for musl target (static linking)
    let target = env::var("TARGET").unwrap_or_default();
    let is_musl = target.contains("musl");
    
    if is_musl {
        println!("cargo:rustc-link-arg=-static");
        // For musl, we rely on the bundled feature of duckdb crate
        // which handles the static compilation internally
    }
    
    // The duckdb crate with "bundled" feature handles DuckDB compilation
    // through its own build script. We don't need to manually build DuckDB here
    // as the bundled feature compiles it from source with all required extensions.
    
    // Set environment variables that the duckdb crate's build script will use
    if env::var("DUCKDB_STATIC_BUILD").is_err() {
        println!("cargo:rustc-env=DUCKDB_STATIC_BUILD=1");
    }
    
    // Enable required DuckDB extensions at build time
    // These are built into the static library
    let extensions = vec![
        "json",
        "fts", 
        "vss",
        "core_functions",
        "jemalloc"
    ];
    
    let extensions_str = extensions.join(";");
    if env::var("BUILD_EXTENSIONS").is_err() {
        env::set_var("BUILD_EXTENSIONS", &extensions_str);
        println!("cargo:rustc-env=BUILD_EXTENSIONS={}", extensions_str);
    }
    
    // Disable builtin extensions to avoid conflicts
    if env::var("DISABLE_BUILTIN_EXTENSIONS").is_err() {
        println!("cargo:rustc-env=DISABLE_BUILTIN_EXTENSIONS=0");  
    }
    
    // Static C++ linking for musl
    if is_musl {
        println!("cargo:rustc-env=STATIC_LIBCPP=1");
    }
    
    println!("cargo:rustc-link-lib=static=stdc++");
    
    // Add system libraries required for static linking
    if is_musl {
        let system_libs = vec![
            "pthread",
            "dl", 
            "m",
            "rt",
        ];
        
        for lib in system_libs {
            println!("cargo:rustc-link-lib=static={}", lib);
        }
    }
}