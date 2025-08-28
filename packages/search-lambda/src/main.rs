use lambda_runtime::{service_fn, Error, LambdaEvent};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::sync::OnceLock;
use tracing::{info, warn, error, debug};

use fdnix_search_lambda::*;

// Global clients (initialized once)
static LANCEDB_CLIENT: OnceLock<Option<LanceDBClient>> = OnceLock::new();
static BEDROCK_CLIENT: OnceLock<Option<BedrockClient>> = OnceLock::new();

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


async fn handle_search_request(
    query: String, 
    limit: i32, 
    offset: i32, 
    license_filter: Option<String>,
    category_filter: Option<String>
) -> Result<SearchResponseBody, Box<dyn std::error::Error + Send + Sync>> {
    // Get clients from static storage
    let lancedb_client = LANCEDB_CLIENT.get()
        .ok_or("LanceDB client not initialized - this should not happen as Lambda should fail at startup")?
        .as_ref()
        .ok_or("LanceDB client failed to initialize during startup - check Lambda layer and LANCEDB_PATH configuration")?;

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




async fn initialize_clients() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    info!("Starting fdnix-search-api Rust Lambda v{}", env!("CARGO_PKG_VERSION"));
    debug!("Lambda initialization starting with environment configuration");

    // Log environment configuration
    debug!("Environment variables: LANCEDB_PATH={:?}, AWS_REGION={:?}, ENABLE_EMBEDDINGS={:?}", 
           env::var("LANCEDB_PATH").ok(), 
           env::var("AWS_REGION").ok(), 
           env::var("ENABLE_EMBEDDINGS").ok());

    // Initialize LanceDB client with path validation and fallback
    let lancedb_client = match get_lancedb_path().await {
        Ok(lancedb_path) => {
            info!("Initializing LanceDB client with validated path: {}", lancedb_path);
            let start_time = std::time::Instant::now();
            match LanceDBClient::new(&lancedb_path) {
                Ok(mut client) => {
                    match client.initialize().await {
                        Ok(_) => {
                            let elapsed = start_time.elapsed();
                            info!("LanceDB client initialized successfully in {}ms", elapsed.as_millis());
                            Some(client)
                        }
                        Err(e) => {
                            error!("Failed to initialize LanceDB client after {}ms: {}", start_time.elapsed().as_millis(), e);
                            return Err(format!("LanceDB initialization failed: {}", e).into());
                        }
                    }
                }
                Err(e) => {
                    error!("Failed to create LanceDB client after {}ms: {}", start_time.elapsed().as_millis(), e);
                    return Err(format!("LanceDB client creation failed: {}", e).into());
                }
            }
        }
        Err(e) => {
            error!("Failed to find valid LanceDB path: {}", e);
            return Err(e.into());
        }
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

    // Initialize clients - fail fast if initialization fails
    if let Err(e) = initialize_clients().await {
        error!("Fatal error initializing clients: {}", e);
        panic!("Lambda cannot start without proper client initialization: {}", e);
    }

    // Run the Lambda runtime
    lambda_runtime::run(service_fn(function_handler)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use rstest::*;
    use serde_json::json;
    use std::collections::HashMap;

    #[fixture]
    fn sample_api_gateway_request() -> ApiGatewayRequest {
        let mut query_params = HashMap::new();
        query_params.insert("q".to_string(), "nodejs".to_string());
        query_params.insert("limit".to_string(), "10".to_string());
        query_params.insert("offset".to_string(), "0".to_string());

        ApiGatewayRequest {
            query_string_parameters: Some(query_params),
            body: None,
            headers: None,
        }
    }

    #[fixture]
    fn empty_api_gateway_request() -> ApiGatewayRequest {
        ApiGatewayRequest {
            query_string_parameters: None,
            body: None,
            headers: None,
        }
    }

    #[test]
    fn test_extract_query_params_with_all_parameters() {
        let mut params = HashMap::new();
        params.insert("q".to_string(), "rust compiler".to_string());
        params.insert("limit".to_string(), "25".to_string());
        params.insert("offset".to_string(), "10".to_string());
        params.insert("license".to_string(), "MIT".to_string());
        params.insert("category".to_string(), "development".to_string());

        let (query, limit, offset, license_filter, category_filter) = 
            extract_query_params(&Some(params));

        assert_eq!(query, "rust compiler");
        assert_eq!(limit, 25);
        assert_eq!(offset, 10);
        assert_eq!(license_filter, Some("MIT".to_string()));
        assert_eq!(category_filter, Some("development".to_string()));
    }

    #[test]
    fn test_extract_query_params_with_defaults() {
        let (query, limit, offset, license_filter, category_filter) = 
            extract_query_params(&None);

        assert_eq!(query, "");
        assert_eq!(limit, 50);
        assert_eq!(offset, 0);
        assert_eq!(license_filter, None);
        assert_eq!(category_filter, None);
    }

    #[test]
    fn test_extract_query_params_with_invalid_numbers() {
        let mut params = HashMap::new();
        params.insert("q".to_string(), "test".to_string());
        params.insert("limit".to_string(), "invalid".to_string());
        params.insert("offset".to_string(), "not_a_number".to_string());

        let (query, limit, offset, license_filter, category_filter) = 
            extract_query_params(&Some(params));

        assert_eq!(query, "test");
        assert_eq!(limit, 50); // Should use default
        assert_eq!(offset, 0); // Should use default
        assert_eq!(license_filter, None);
        assert_eq!(category_filter, None);
    }

    #[rstest]
    #[case("5", 5)]
    #[case("100", 100)]
    #[case("0", 0)]
    #[case("-5", -5)]
    fn test_extract_query_params_limit_parsing(
        #[case] limit_str: &str,
        #[case] expected_limit: i32,
    ) {
        let mut params = HashMap::new();
        params.insert("limit".to_string(), limit_str.to_string());

        let (_, limit, _, _, _) = extract_query_params(&Some(params));
        assert_eq!(limit, expected_limit);
    }

    #[rstest]
    #[case("0", 0)]
    #[case("10", 10)]
    #[case("50", 50)]
    #[case("-10", -10)]
    fn test_extract_query_params_offset_parsing(
        #[case] offset_str: &str,
        #[case] expected_offset: i32,
    ) {
        let mut params = HashMap::new();
        params.insert("offset".to_string(), offset_str.to_string());

        let (_, _, offset, _, _) = extract_query_params(&Some(params));
        assert_eq!(offset, expected_offset);
    }

    #[test]
    fn test_is_embeddings_enabled_various_values() {
        // Test with no environment variable
        std::env::remove_var("ENABLE_EMBEDDINGS");
        assert!(!is_embeddings_enabled());

        // Test with "1"
        std::env::set_var("ENABLE_EMBEDDINGS", "1");
        assert!(is_embeddings_enabled());

        // Test with "true" (case insensitive)
        std::env::set_var("ENABLE_EMBEDDINGS", "true");
        assert!(is_embeddings_enabled());

        std::env::set_var("ENABLE_EMBEDDINGS", "TRUE");
        assert!(is_embeddings_enabled());

        // Test with "yes" (case insensitive)
        std::env::set_var("ENABLE_EMBEDDINGS", "yes");
        assert!(is_embeddings_enabled());

        std::env::set_var("ENABLE_EMBEDDINGS", "YES");
        assert!(is_embeddings_enabled());

        // Test with "false"
        std::env::set_var("ENABLE_EMBEDDINGS", "false");
        assert!(!is_embeddings_enabled());

        // Test with "0"
        std::env::set_var("ENABLE_EMBEDDINGS", "0");
        assert!(!is_embeddings_enabled());

        // Test with random value
        std::env::set_var("ENABLE_EMBEDDINGS", "random");
        assert!(!is_embeddings_enabled());

        // Clean up
        std::env::remove_var("ENABLE_EMBEDDINGS");
    }

    #[tokio::test]
    async fn test_get_lancedb_path_with_env_var() {
        let temp_dir = tempfile::tempdir().unwrap();
        let db_path = temp_dir.path().join("test.lance");
        std::fs::create_dir_all(&db_path).unwrap();
        
        // Create required directories for a valid LanceDB structure
        std::fs::create_dir_all(db_path.join("data")).unwrap();
        std::fs::create_dir_all(db_path.join("_versions")).unwrap();
        
        std::env::set_var("LANCEDB_PATH", db_path.to_str().unwrap());
        
        let result = get_lancedb_path().await;
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), db_path.to_str().unwrap());
        
        std::env::remove_var("LANCEDB_PATH");
    }

    #[tokio::test]
    async fn test_get_lancedb_path_no_valid_path() {
        std::env::remove_var("LANCEDB_PATH");
        
        let result = get_lancedb_path().await;
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("No valid LanceDB database found"));
    }

    #[tokio::test]
    async fn test_get_lancedb_path_missing_structure() {
        let temp_dir = tempfile::tempdir().unwrap();
        let db_path = temp_dir.path().join("invalid.lance");
        std::fs::create_dir_all(&db_path).unwrap();
        // Don't create data/ and _versions/ directories
        
        std::env::set_var("LANCEDB_PATH", db_path.to_str().unwrap());
        
        let result = get_lancedb_path().await;
        assert!(result.is_err());
        
        std::env::remove_var("LANCEDB_PATH");
    }

    #[tokio::test]
    async fn test_create_health_check_response() {
        std::env::set_var("LANCEDB_PATH", "/test/path");
        std::env::set_var("BEDROCK_MODEL_ID", "test-model");
        std::env::set_var("AWS_REGION", "us-west-2");
        std::env::set_var("ENABLE_EMBEDDINGS", "true");
        
        let response = create_health_check_response("test query".to_string()).await;
        
        assert_eq!(response.message, "fdnix search API (Rust) — stub active");
        assert_eq!(response.version, Some("0.1.0".to_string()));
        assert_eq!(response.runtime, Some("provided.al2023".to_string()));
        assert_eq!(response.query_received, Some("test query".to_string()));
        assert_eq!(response.lancedb_path, Some("/test/path".to_string()));
        assert_eq!(response.bedrock_model_id, Some("test-model".to_string()));
        assert_eq!(response.aws_region, Some("us-west-2".to_string()));
        assert_eq!(response.enable_embeddings, Some("true".to_string()));
        
        // Clean up
        std::env::remove_var("LANCEDB_PATH");
        std::env::remove_var("BEDROCK_MODEL_ID");
        std::env::remove_var("AWS_REGION");
        std::env::remove_var("ENABLE_EMBEDDINGS");
    }

    #[test]
    fn test_api_gateway_request_deserialization() {
        let json_str = r#"{
            "queryStringParameters": {
                "q": "nodejs",
                "limit": "10"
            },
            "body": null,
            "headers": {
                "Content-Type": "application/json"
            }
        }"#;
        
        let request: ApiGatewayRequest = serde_json::from_str(json_str).unwrap();
        
        assert!(request.query_string_parameters.is_some());
        let params = request.query_string_parameters.unwrap();
        assert_eq!(params.get("q"), Some(&"nodejs".to_string()));
        assert_eq!(params.get("limit"), Some(&"10".to_string()));
        
        assert!(request.body.is_none());
        assert!(request.headers.is_some());
    }

    #[test]
    fn test_api_gateway_response_serialization() {
        let mut headers = HashMap::new();
        headers.insert("Content-Type".to_string(), "application/json".to_string());
        
        let response = ApiGatewayResponse {
            status_code: 200,
            headers,
            body: "{\"message\": \"success\"}".to_string(),
        };
        
        let json_str = serde_json::to_string(&response).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        
        assert_eq!(parsed["statusCode"], 200);
        assert_eq!(parsed["headers"]["Content-Type"], "application/json");
        assert!(parsed["body"].is_string());
    }

    #[test]
    fn test_search_response_body_serialization() {
        let response = SearchResponseBody {
            message: "Search completed".to_string(),
            query: Some("nodejs".to_string()),
            total_count: Some(5),
            query_time_ms: Some(42.5),
            search_type: Some("hybrid".to_string()),
            packages: None,
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
        };
        
        let json_str = serde_json::to_string(&response).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        
        assert_eq!(parsed["message"], "Search completed");
        assert_eq!(parsed["query"], "nodejs");
        assert_eq!(parsed["total_count"], 5);
        assert_eq!(parsed["query_time_ms"], 42.5);
        assert_eq!(parsed["search_type"], "hybrid");
    }

    #[test]
    fn test_search_response_body_with_packages() {
        let packages_json = vec![
            json!({
                "packageId": "nodejs-18",
                "packageName": "nodejs",
                "version": "18.17.1",
                "description": "Event-driven I/O framework",
                "homepage": "https://nodejs.org",
                "license": "MIT",
                "attributePath": "pkgs.nodejs",
                "relevanceScore": 0.95
            })
        ];
        
        let response = SearchResponseBody {
            message: "Search completed".to_string(),
            query: Some("nodejs".to_string()),
            total_count: Some(1),
            query_time_ms: Some(25.0),
            search_type: Some("vector".to_string()),
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
        };
        
        let json_str = serde_json::to_string(&response).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        
        assert!(parsed["packages"].is_array());
        let packages_array = parsed["packages"].as_array().unwrap();
        assert_eq!(packages_array.len(), 1);
        assert_eq!(packages_array[0]["packageId"], "nodejs-18");
    }

    #[test]
    fn test_query_param_edge_cases() {
        let mut params = HashMap::new();
        params.insert("q".to_string(), "".to_string()); // Empty query
        params.insert("limit".to_string(), "0".to_string()); // Zero limit
        params.insert("offset".to_string(), "-1".to_string()); // Negative offset
        
        let (query, limit, offset, license_filter, category_filter) = 
            extract_query_params(&Some(params));
        
        assert_eq!(query, "");
        assert_eq!(limit, 0);
        assert_eq!(offset, -1);
        assert_eq!(license_filter, None);
        assert_eq!(category_filter, None);
    }

    #[test]
    fn test_query_param_special_characters() {
        let mut params = HashMap::new();
        params.insert("q".to_string(), "c++/c# programming".to_string());
        params.insert("license".to_string(), "GPL-2.0+".to_string());
        params.insert("category".to_string(), "devel/libs".to_string());
        
        let (query, limit, offset, license_filter, category_filter) = 
            extract_query_params(&Some(params));
        
        assert_eq!(query, "c++/c# programming");
        assert_eq!(limit, 50); // default
        assert_eq!(offset, 0); // default
        assert_eq!(license_filter, Some("GPL-2.0+".to_string()));
        assert_eq!(category_filter, Some("devel/libs".to_string()));
    }
}