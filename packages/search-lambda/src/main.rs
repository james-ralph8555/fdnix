mod lancedb_client;
mod bedrock_client;

use lambda_runtime::{service_fn, Error, LambdaEvent};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::sync::OnceLock;
use tracing::{info, warn, error, debug};

use crate::lancedb_client::{LanceDBClient, SearchParams};
use crate::bedrock_client::BedrockClient;

// Global clients (initialized once)
static LANCEDB_CLIENT: OnceLock<Option<LanceDBClient>> = OnceLock::new();
static BEDROCK_CLIENT: OnceLock<Option<BedrockClient>> = OnceLock::new();

#[derive(Deserialize)]
struct ApiGatewayRequest {
    #[serde(rename = "queryStringParameters")]
    query_string_parameters: Option<HashMap<String, String>>,
    body: Option<String>,
    headers: Option<HashMap<String, String>>,
}

#[derive(Serialize)]
struct ApiGatewayResponse {
    #[serde(rename = "statusCode")]
    status_code: u16,
    headers: HashMap<String, String>,
    body: String,
}

#[derive(Serialize)]
struct SearchResponseBody {
    message: String,
    query: Option<String>,
    total_count: Option<i32>,
    query_time_ms: Option<f64>,
    search_type: Option<String>,
    packages: Option<Vec<Value>>,
    // Status fields for health check
    note: Option<String>,
    version: Option<String>,
    runtime: Option<String>,
    query_received: Option<String>,
    lancedb_path: Option<String>,
    bedrock_model_id: Option<String>,
    aws_region: Option<String>,
    enable_embeddings: Option<String>,
    lancedb_initialized: Option<bool>,
    bedrock_initialized: Option<bool>,
    lancedb_healthy: Option<bool>,
    bedrock_healthy: Option<bool>,
}

async fn function_handler(event: LambdaEvent<Value>) -> Result<ApiGatewayResponse, Error> {
    let payload = event.payload;
    
    // Parse API Gateway request
    let request: Result<ApiGatewayRequest, _> = serde_json::from_value(payload);
    
    let mut response_headers = HashMap::new();
    response_headers.insert("Content-Type".to_string(), "application/json".to_string());
    response_headers.insert("Access-Control-Allow-Origin".to_string(), "*".to_string());
    
    match request {
        Ok(req) => {
            // Extract query parameters
            let (query_param, limit, offset, license_filter, category_filter) = 
                extract_query_params(&req.query_string_parameters);
            
            // Handle search request
            if !query_param.is_empty() {
                match handle_search_request(query_param.clone(), limit, offset, license_filter, category_filter).await {
                    Ok(response_body) => {
                        let body = serde_json::to_string(&response_body)?;
                        Ok(ApiGatewayResponse {
                            status_code: 200,
                            headers: response_headers,
                            body,
                        })
                    }
                    Err(e) => {
                        error!("Search request failed: {}", e);
                        let error_body = json!({
                            "error": "Internal server error",
                            "message": e.to_string()
                        });
                        Ok(ApiGatewayResponse {
                            status_code: 500,
                            headers: response_headers,
                            body: serde_json::to_string(&error_body)?,
                        })
                    }
                }
            } else {
                // Default health check response
                let response_body = create_health_check_response(query_param).await;
                let body = serde_json::to_string(&response_body)?;
                Ok(ApiGatewayResponse {
                    status_code: 200,
                    headers: response_headers,
                    body,
                })
            }
        }
        Err(e) => {
            error!("Failed to parse request: {}", e);
            let error_body = json!({
                "error": "Bad request",
                "message": "Failed to parse request body"
            });
            Ok(ApiGatewayResponse {
                status_code: 400,
                headers: response_headers,
                body: serde_json::to_string(&error_body)?,
            })
        }
    }
}

fn extract_query_params(params: &Option<HashMap<String, String>>) -> (String, i32, i32, Option<String>, Option<String>) {
    let mut query = String::new();
    let mut limit = 50;
    let mut offset = 0;
    let mut license_filter = None;
    let mut category_filter = None;
    
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
    }
    
    (query, limit, offset, license_filter, category_filter)
}

async fn handle_search_request(
    query: String, 
    limit: i32, 
    offset: i32, 
    license_filter: Option<String>,
    category_filter: Option<String>
) -> Result<SearchResponseBody, Box<dyn std::error::Error + Send + Sync>> {
    // Get clients from static storage
    let lancedb_client = LANCEDB_CLIENT.get()
        .ok_or("LanceDB client not initialized")?
        .as_ref()
        .ok_or("LanceDB client failed to initialize")?;

    // Prepare search parameters
    let search_params = SearchParams {
        query: query.clone(),
        limit,
        offset,
        license_filter,
        category_filter,
    };

    // Check if embeddings are enabled and generate if needed
    let query_embedding = if is_embeddings_enabled() {
        if let Some(bedrock_client) = BEDROCK_CLIENT.get()
            .and_then(|c| c.as_ref()) {
            debug!("Generating embedding for search query: '{}'", query.chars().take(50).collect::<String>());
            match bedrock_client.generate_embedding(&query).await {
                Ok(embedding) => {
                    info!("Successfully generated query embedding with {} dimensions", embedding.len());
                    Some(embedding)
                }
                Err(e) => {
                    warn!("Failed to generate query embedding, falling back to FTS-only: {}", e);
                    None
                }
            }
        } else {
            warn!("Bedrock client not available for embedding generation");
            None
        }
    } else {
        debug!("Embeddings disabled, using FTS-only search");
        None
    };

    // Perform search
    debug!("Executing hybrid search with query: '{}', limit: {}, offset: {}", query, limit, offset);
    let search_start = std::time::Instant::now();
    let results = lancedb_client.hybrid_search(&search_params, query_embedding.as_deref()).await?;
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
        // Health check fields not used in search response
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

async fn create_health_check_response(query_param: String) -> SearchResponseBody {
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

fn is_embeddings_enabled() -> bool {
    env::var("ENABLE_EMBEDDINGS")
        .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
        .unwrap_or(false)
}

async fn initialize_clients() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    info!("Starting fdnix-search-api Rust Lambda v{}", env!("CARGO_PKG_VERSION"));
    debug!("Lambda initialization starting with environment configuration");

    // Log environment configuration
    debug!("Environment variables: LANCEDB_PATH={:?}, AWS_REGION={:?}, ENABLE_EMBEDDINGS={:?}", 
           env::var("LANCEDB_PATH").ok(), 
           env::var("AWS_REGION").ok(), 
           env::var("ENABLE_EMBEDDINGS").ok());

    // Initialize LanceDB client
    let lancedb_client = if let Ok(lancedb_path) = env::var("LANCEDB_PATH") {
        info!("Initializing LanceDB client with path: {}", lancedb_path);
        let start_time = std::time::Instant::now();
        match LanceDBClient::new(&lancedb_path) {
            Ok(mut client) => {
                if client.initialize().await? {
                    let elapsed = start_time.elapsed();
                    info!("LanceDB client initialized successfully in {}ms", elapsed.as_millis());
                    Some(client)
                } else {
                    error!("Failed to initialize LanceDB client after {}ms", start_time.elapsed().as_millis());
                    None
                }
            }
            Err(e) => {
                error!("Failed to create LanceDB client after {}ms: {}", start_time.elapsed().as_millis(), e);
                None
            }
        }
    } else {
        error!("LANCEDB_PATH environment variable not set");
        None
    };

    // Initialize Bedrock client only if embeddings are enabled
    let bedrock_client = if is_embeddings_enabled() {
        let aws_region = env::var("AWS_REGION").unwrap_or_else(|_| "us-east-1".to_string());
        let bedrock_model = env::var("BEDROCK_MODEL_ID")
            .unwrap_or_else(|_| "amazon.titan-embed-text-v2:0".to_string());
        let dimensions = env::var("BEDROCK_OUTPUT_DIMENSIONS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(256);

        info!("Initializing Bedrock client with model: {}, region: {}, dimensions: {}", bedrock_model, aws_region, dimensions);
        let start_time = std::time::Instant::now();
        
        match BedrockClient::new(&aws_region, &bedrock_model, dimensions).await {
            Ok(client) => {
                let elapsed = start_time.elapsed();
                info!("Bedrock client initialized successfully in {}ms", elapsed.as_millis());
                Some(client)
            }
            Err(e) => {
                error!("Failed to initialize Bedrock client after {}ms: {}", start_time.elapsed().as_millis(), e);
                None
            }
        }
    } else {
        info!("Embeddings disabled, skipping Bedrock client initialization");
        None
    };

    // Store clients in static storage
    LANCEDB_CLIENT.set(lancedb_client).map_err(|_| "Failed to set LanceDB client")?;
    BEDROCK_CLIENT.set(bedrock_client).map_err(|_| "Failed to set Bedrock client")?;

    info!("Lambda initialization complete - LanceDB: {}, Bedrock: {}", 
          LANCEDB_CLIENT.get().and_then(|c| c.as_ref()).is_some(),
          BEDROCK_CLIENT.get().and_then(|c| c.as_ref()).is_some());
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_target(false)
        .without_time()
        .init();

    // Initialize clients
    if let Err(e) = initialize_clients().await {
        error!("Error initializing clients: {}", e);
    }

    // Run the Lambda runtime
    lambda_runtime::run(service_fn(function_handler)).await
}