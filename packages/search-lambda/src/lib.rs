pub mod lancedb_client;
pub mod bedrock_client;

pub use lancedb_client::*;
pub use bedrock_client::*;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::sync::OnceLock;
use tracing::{info, warn, debug};

// Global clients (initialized once)
static LANCEDB_CLIENT: OnceLock<Option<LanceDBClient>> = OnceLock::new();
static BEDROCK_CLIENT: OnceLock<Option<BedrockClient>> = OnceLock::new();

#[derive(Deserialize, Debug, Clone)]
pub struct ApiGatewayRequest {
    #[serde(rename = "queryStringParameters")]
    pub query_string_parameters: Option<HashMap<String, String>>,
    pub body: Option<String>,
    pub headers: Option<HashMap<String, String>>,
}

#[derive(Serialize, Debug, Clone)]
pub struct ApiGatewayResponse {
    #[serde(rename = "statusCode")]
    pub status_code: u16,
    pub headers: HashMap<String, String>,
    pub body: String,
}

#[derive(Serialize, Debug, Clone)]
pub struct SearchResponseBody {
    pub message: String,
    pub query: Option<String>,
    pub total_count: Option<i32>,
    pub query_time_ms: Option<f64>,
    pub search_type: Option<String>,
    pub packages: Option<Vec<Value>>,
    // Status fields for health check
    pub note: Option<String>,
    pub version: Option<String>,
    pub runtime: Option<String>,
    pub query_received: Option<String>,
    pub lancedb_path: Option<String>,
    pub bedrock_model_id: Option<String>,
    pub aws_region: Option<String>,
    pub enable_embeddings: Option<String>,
    pub lancedb_initialized: Option<bool>,
    pub bedrock_initialized: Option<bool>,
    pub lancedb_healthy: Option<bool>,
    pub bedrock_healthy: Option<bool>,
}

pub fn extract_query_params(params: &Option<HashMap<String, String>>) -> (String, i32, i32, Option<String>, Option<String>, bool, bool) {
    let mut query = String::new();
    let mut limit = 50;
    let mut offset = 0;
    let mut license_filter = None;
    let mut category_filter = None;
    let mut include_broken = false;
    let mut include_unfree = false;
    
    if let Some(params) = params {
        if let Some(q) = params.get("q") {
            query = q.clone();
        }
        if let Some(l) = params.get("limit") {
            if let Ok(l_val) = l.parse::<i32>() {
                limit = l_val;
            }
        }
        if let Some(o) = params.get("offset") {
            if let Ok(o_val) = o.parse::<i32>() {
                offset = o_val;
            }
        }
        if let Some(license) = params.get("license") {
            license_filter = Some(license.clone());
        }
        if let Some(category) = params.get("category") {
            category_filter = Some(category.clone());
        }
        // Parse boolean parameters for broken/unfree packages
        if let Some(broken_str) = params.get("include_broken") {
            include_broken = broken_str == "1" || broken_str.to_lowercase() == "true" || broken_str.to_lowercase() == "yes";
        }
        if let Some(unfree_str) = params.get("include_unfree") {
            include_unfree = unfree_str == "1" || unfree_str.to_lowercase() == "true" || unfree_str.to_lowercase() == "yes";
        }
    }
    
    (query, limit, offset, license_filter, category_filter, include_broken, include_unfree)
}

pub fn is_embeddings_enabled() -> bool {
    env::var("ENABLE_EMBEDDINGS")
        .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
        .unwrap_or(false)
}

pub async fn get_lancedb_path() -> Result<String, String> {
    use std::path::Path;
    
    // Try paths in priority order: env var, Lambda layer paths, local paths
    let candidate_paths = vec![
        env::var("LANCEDB_PATH").unwrap_or_default(),
        "/opt/fdnix/fdnix.lancedb".to_string(),  // Original expected path
        "/opt".to_string(),                      // Lambda layer root where packages.lance should be extracted
        ".".to_string(),                         // Current directory
        "./packages.lance".to_string(),          // Fallback to packages.lance directory
        "packages.lance".to_string(),            // Fallback to packages.lance directory
    ];
    
    for path in &candidate_paths {
        if path.is_empty() {
            continue;
        }
        
        info!("Checking database path: {}", path);
        
        let path_obj = Path::new(path);
        if path_obj.exists() && path_obj.is_dir() {
            // First, check if this directory itself has LanceDB structure (direct database)
            let data_dir = path_obj.join("data");
            let versions_dir = path_obj.join("_versions");
            
            if data_dir.exists() && versions_dir.exists() {
                info!("Valid LanceDB structure found at: {}", path);
                
                // List contents for debugging
                if let Ok(entries) = std::fs::read_dir(path) {
                    let contents: Vec<String> = entries
                        .filter_map(|e| e.ok())
                        .map(|e| e.file_name().to_string_lossy().to_string())
                        .collect();
                    debug!("Database directory contents: {:?}", contents);
                }
                
                return Ok(path.clone());
            }
            
            // Otherwise, check if this directory contains packages.lance subdirectory
            let packages_lance_dir = path_obj.join("packages.lance");
            if packages_lance_dir.exists() && packages_lance_dir.is_dir() {
                let data_dir = packages_lance_dir.join("data");
                let versions_dir = packages_lance_dir.join("_versions");
                
                if data_dir.exists() && versions_dir.exists() {
                    info!("Valid LanceDB structure found at: {}", packages_lance_dir.display());
                    
                    // List contents for debugging
                    if let Ok(entries) = std::fs::read_dir(&packages_lance_dir) {
                        let contents: Vec<String> = entries
                            .filter_map(|e| e.ok())
                            .map(|e| e.file_name().to_string_lossy().to_string())
                            .collect();
                        debug!("Database directory contents: {:?}", contents);
                    }
                    
                    // Return the parent directory path, as LanceDB expects to connect to the parent
                    return Ok(path.clone());
                } else {
                    warn!("packages.lance directory exists but missing LanceDB structure at: {} (data: {}, versions: {})", 
                          packages_lance_dir.display(), data_dir.exists(), versions_dir.exists());
                }
            } else {
                debug!("No packages.lance subdirectory found in: {}", path);
            }
        } else {
            debug!("Path does not exist or is not a directory: {}", path);
        }
    }
    
    // If we reach here, no valid path was found
    let attempted_paths = candidate_paths.into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<String>>()
        .join(", ");
    
    Err(format!("No valid LanceDB database found. Attempted paths: [{}]. Ensure the database layer is properly attached and contains packages.lance directory with data/ and _versions/ subdirectories.", attempted_paths))
}

pub async fn create_health_check_response(query_param: String) -> SearchResponseBody {
    let mut response = SearchResponseBody {
        message: "fdnix search API (Rust) — stub active".to_string(),
        note: Some("This is a Rust Lambda stub. LanceDB integration ready.".to_string()),
        version: Some("0.1.0".to_string()),
        runtime: Some("provided.al2023".to_string()),
        query: None,
        total_count: None,
        query_time_ms: None,
        search_type: None,
        packages: None,
        query_received: if !query_param.is_empty() { Some(query_param) } else { None },
        lancedb_path: env::var("LANCEDB_PATH").ok(),
        bedrock_model_id: env::var("BEDROCK_MODEL_ID").ok(),
        aws_region: env::var("AWS_REGION").ok(),
        enable_embeddings: env::var("ENABLE_EMBEDDINGS").ok(),
        lancedb_initialized: Some(LANCEDB_CLIENT.get().and_then(|c| c.as_ref()).is_some()),
        bedrock_initialized: Some(BEDROCK_CLIENT.get().and_then(|c| c.as_ref()).is_some()),
        lancedb_healthy: None,
        bedrock_healthy: None,
    };

    // Check client health
    if let Some(lancedb_client) = LANCEDB_CLIENT.get().and_then(|c| c.as_ref()) {
        response.lancedb_healthy = Some(lancedb_client.health_check().await);
    }

    if let Some(bedrock_client) = BEDROCK_CLIENT.get().and_then(|c| c.as_ref()) {
        response.bedrock_healthy = Some(bedrock_client.health_check().await.unwrap_or(false));
    }

    response
}