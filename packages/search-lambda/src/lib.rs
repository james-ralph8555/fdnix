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
        json!({
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
        })
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
    let sqlite_client = match get_sqlite_path().await {
        Ok(sqlite_path) => {
            info!("Initializing SQLite client with path: {}", sqlite_path);
            let start_time = std::time::Instant::now();
            match SQLiteClient::new(&sqlite_path) {
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
            return Err(format!("Failed to find valid SQLite path: {}", e).into());
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


pub async fn get_sqlite_path() -> Result<String, String> {
    // SQLite database path
    let sqlite_path = "/opt/fdnix/fdnix.db";
    
    info!("Checking SQLite database path: {}", sqlite_path);
    
    if std::path::Path::new(sqlite_path).exists() {
        info!("Valid SQLite database found at: {}", sqlite_path);
        Ok(sqlite_path.to_string())
    } else {
        Err(format!("SQLite database not found at: {}. Ensure the database layer is properly attached.", sqlite_path))
    }
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

    // Check SQLite client health
    if let Some(sqlite_client) = SQLITE_CLIENT.get().and_then(|c| c.as_ref()) {
        response.lancedb_healthy = Some(sqlite_client.health_check().await);
    }

    response
}