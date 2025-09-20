pub mod sqlite_client;

pub use sqlite_client::*;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::env;
use std::sync::OnceLock;
use tracing::{info};

// Global client (initialized once)
static SQLITE_CLIENT: OnceLock<Option<SQLiteClient>> = OnceLock::new();

pub async fn handle_search_request(
    query: String, 
    limit: i32, 
    offset: i32, 
    license_filter: Option<String>,
    category_filter: Option<String>,
    include_broken: bool,
    include_unfree: bool
) -> Result<SearchResponseBody, Box<dyn std::error::Error + Send + Sync>> {
    // Get SQLite client from static storage
    let sqlite_client = SQLITE_CLIENT.get()
        .ok_or("SQLite client not initialized - this should not happen as Lambda should fail at startup")?
        .as_ref()
        .ok_or("SQLite client failed to initialize during startup - check Lambda layer and database configuration")?;

    // Prepare search parameters
    let search_params = SearchParams {
        query: query.clone(),
        limit,
        offset,
        license_filter,
        category_filter,
        include_broken,
        include_unfree,
    };

    // Perform FTS search
    let search_start = std::time::Instant::now();
    let results = sqlite_client.fts_search(&search_params).await?;
    let search_elapsed = search_start.elapsed();
    
    info!(
        "Search completed: type={}, results={}, duration={}ms", 
        results.search_type, 
        results.total_count,
        search_elapsed.as_millis()
    );

    // Convert packages to JSON values for response
    let packages_json: Vec<Value> = results.packages.into_iter().map(|pkg| {
        let mut package_json = json!({
            "packageId": pkg.package_id,
            "packageName": pkg.package_name,
            "version": pkg.version,
            "description": pkg.description,
            "homepage": pkg.homepage,
            "license": pkg.license,
            "attributePath": pkg.attribute_path,
            "category": pkg.category,
            "broken": pkg.broken,
            "unfree": pkg.unfree,
            "available": pkg.available,
            "relevanceScore": pkg.relevance_score
        });
        
        // Add additional fields if available (from minified data)
        if let Some(maintainers) = pkg.maintainers {
            package_json["maintainers"] = Value::Array(maintainers);
        }
        if let Some(platforms) = pkg.platforms {
            package_json["platforms"] = Value::Array(
                platforms.into_iter().map(Value::String).collect()
            );
        }
        if let Some(long_desc) = pkg.long_description {
            package_json["longDescription"] = Value::String(long_desc);
        }
        if let Some(main_program) = pkg.main_program {
            package_json["mainProgram"] = Value::String(main_program);
        }
        if let Some(position) = pkg.position {
            package_json["position"] = Value::String(position);
        }
        if let Some(outputs) = pkg.outputs_to_install {
            package_json["outputsToInstall"] = Value::Array(
                outputs.into_iter().map(Value::String).collect()
            );
        }
        if let Some(last_updated) = pkg.last_updated {
            package_json["lastUpdated"] = Value::String(last_updated);
        }
        
        package_json
    }).collect();

    Ok(SearchResponseBody {
        message: "Search completed".to_string(),
        query: Some(query),
        total_count: Some(results.total_count),
        query_time_ms: Some(results.query_time_ms),
        search_type: Some(results.search_type),
        packages: Some(packages_json),
        note: None,
        version: None,
        runtime: None,
        query_received: None,
        lancedb_path: None,
        bedrock_model_id: None,
        aws_region: None,
        enable_embeddings: None,
        lancedb_initialized: None,
        bedrock_initialized: None,
        lancedb_healthy: None,
        bedrock_healthy: None,
    })
}

pub async fn initialize_clients() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    info!("Starting fdnix-search-api Rust Lambda v{}", env!("CARGO_PKG_VERSION"));

    // Initialize SQLite client with path validation
    let sqlite_client = match get_sqlite_paths().await {
        Ok((sqlite_path, dict_path)) => {
            info!("Initializing SQLite client with database: {}, dictionary: {}", sqlite_path, dict_path);
            let start_time = std::time::Instant::now();
            match SQLiteClient::new(&sqlite_path, &dict_path) {
                Ok(mut client) => {
                    match client.initialize().await {
                        Ok(_) => {
                            let elapsed = start_time.elapsed();
                            info!("SQLite client initialized successfully in {}ms", elapsed.as_millis());
                            Some(client)
                        }
                        Err(e) => {
                            return Err(format!("SQLite initialization failed: {}", e).into());
                        }
                    }
                }
                Err(e) => {
                    return Err(format!("SQLite client creation failed: {}", e).into());
                }
            }
        }
        Err(e) => {
            return Err(format!("Failed to find valid SQLite paths: {}", e).into());
        }
    };

    // Store client in static storage
    SQLITE_CLIENT.set(sqlite_client).map_err(|_| "Failed to set SQLite client")?;

    info!("Lambda initialization complete - SQLite: {}", 
          SQLITE_CLIENT.get().and_then(|c| c.as_ref()).is_some());
    Ok(())
}

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


pub async fn get_sqlite_paths() -> Result<(String, String), String> {
    // Check for minified database and dictionary
    let db_path = "/opt/fdnix/minified.db";
    let dict_path = "/opt/fdnix/shared.dict";
    
    // Validate both files exist
    if !std::path::Path::new(db_path).exists() {
        return Err(format!("Minified SQLite database not found at: {}", db_path));
    }
    
    if !std::path::Path::new(dict_path).exists() {
        return Err(format!("Compression dictionary not found at: {}", dict_path));
    }
    
    info!("Valid minified SQLite database found at: {}", db_path);
    info!("Valid compression dictionary found at: {}", dict_path);
    
    Ok((db_path.to_string(), dict_path.to_string()))
}

pub async fn create_health_check_response(query_param: String) -> SearchResponseBody {
    let mut response = SearchResponseBody {
        message: "fdnix search API (Rust) — SQLite FTS active".to_string(),
        note: Some("This is a Rust Lambda with SQLite FTS search.".to_string()),
        version: Some("0.1.0".to_string()),
        runtime: Some("provided.al2023".to_string()),
        query: None,
        total_count: None,
        query_time_ms: None,
        search_type: None,
        packages: None,
        query_received: if !query_param.is_empty() { Some(query_param) } else { None },
        lancedb_path: None,
        bedrock_model_id: None,
        aws_region: env::var("AWS_REGION").ok(),
        enable_embeddings: None,
        lancedb_initialized: None,
        bedrock_initialized: None,
        lancedb_healthy: None,
        bedrock_healthy: None,
    };

    // Check SQLite client health and schema type
    if let Some(sqlite_client) = SQLITE_CLIENT.get().and_then(|c| c.as_ref()) {
        let is_healthy = sqlite_client.health_check().await;
        response.lancedb_healthy = Some(is_healthy);
        
        if is_healthy {
            // Determine schema type from client state
            {
                response.message = "fdnix search API (Rust) — SQLite FTS with minified data active".to_string();
                response.note = Some("This is a Rust Lambda with SQLite FTS search using Zstandard-compressed data.".to_string());
          }
    }

    response
}