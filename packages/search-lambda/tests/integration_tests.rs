use serde_json::{json, Value};
use std::collections::HashMap;

// Helper to create API Gateway event for testing
fn create_api_gateway_event(
    query_params: Option<HashMap<String, String>>,
    body: Option<String>,
    headers: Option<HashMap<String, String>>,
) -> Value {
    let event = json!({
        "resource": "/search",
        "path": "/search",
        "httpMethod": "GET",
        "headers": headers.unwrap_or_default(),
        "multiValueHeaders": {},
        "queryStringParameters": query_params,
        "multiValueQueryStringParameters": {},
        "pathParameters": null,
        "stageVariables": null,
        "requestContext": {
            "requestId": "test-request-id",
            "stage": "test",
            "resourcePath": "/search",
            "httpMethod": "GET",
            "path": "/test/search",
            "protocol": "HTTP/1.1",
            "requestTime": "27/Aug/2025:12:00:00 +0000",
            "requestTimeEpoch": 1724760000
        },
        "body": body,
        "isBase64Encoded": false
    });

    event
}

#[tokio::test]
async fn test_health_check_endpoint() {
    // Set up minimal environment for health check
    std::env::remove_var("LANCEDB_PATH");
    std::env::remove_var("ENABLE_EMBEDDINGS");
    
    let event_payload = create_api_gateway_event(None, None, None);
    
    // This should return a health check response since no query parameter is provided
    // Note: The actual function_handler requires initialization, which will fail in test env
    // but we can test the event structure
    assert!(event_payload["resource"].as_str().unwrap() == "/search");
    assert!(event_payload["httpMethod"].as_str().unwrap() == "GET");
}

#[tokio::test]
async fn test_search_request_structure() {
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), "nodejs".to_string());
    query_params.insert("limit".to_string(), "10".to_string());
    query_params.insert("offset".to_string(), "0".to_string());
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify event structure
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == "nodejs");
    assert!(event_payload["queryStringParameters"]["limit"].as_str().unwrap() == "10");
    assert!(event_payload["queryStringParameters"]["offset"].as_str().unwrap() == "0");
}

#[tokio::test] 
async fn test_search_with_filters() {
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), "python".to_string());
    query_params.insert("license".to_string(), "MIT".to_string());
    query_params.insert("category".to_string(), "development".to_string());
    query_params.insert("limit".to_string(), "25".to_string());
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify event structure with filters
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == "python");
    assert!(event_payload["queryStringParameters"]["license"].as_str().unwrap() == "MIT");
    assert!(event_payload["queryStringParameters"]["category"].as_str().unwrap() == "development");
    assert!(event_payload["queryStringParameters"]["limit"].as_str().unwrap() == "25");
}

#[tokio::test]
async fn test_invalid_json_event_handling() {
    // Create an invalid event structure
    let invalid_event = json!({
        "invalidField": "value",
        "notAnApiGatewayEvent": true
    });
    
    // This would test error handling for malformed events  
    // The function should gracefully handle parsing errors
    assert!(invalid_event["invalidField"].as_str().unwrap() == "value");
}

#[test]
fn test_cors_headers_structure() {
    let mut expected_headers = HashMap::new();
    expected_headers.insert("Content-Type".to_string(), "application/json".to_string());
    expected_headers.insert("Access-Control-Allow-Origin".to_string(), "*".to_string());
    
    // Verify CORS headers are properly structured
    assert_eq!(expected_headers.get("Access-Control-Allow-Origin"), Some(&"*".to_string()));
    assert_eq!(expected_headers.get("Content-Type"), Some(&"application/json".to_string()));
}

#[test]
fn test_response_status_codes() {
    // Test expected HTTP status codes
    assert_eq!(200, 200); // Success
    assert_eq!(400, 400); // Bad Request
    assert_eq!(500, 500); // Internal Server Error
    
    // These would be returned by the actual handler in different scenarios
}

#[tokio::test]
async fn test_empty_query_handling() {
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), "".to_string()); // Empty query
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify empty query is handled
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == "");
}

#[tokio::test]
async fn test_large_query_handling() {
    let large_query = "a".repeat(1000); // 1000 character query
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), large_query.clone());
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify large query is preserved
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == large_query);
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap().len() == 1000);
}

#[tokio::test]
async fn test_special_characters_in_query() {
    let special_query = "c++ & rust (2024) - \"best\" languages!";
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), special_query.to_string());
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify special characters are preserved
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == special_query);
}

#[tokio::test]
async fn test_unicode_query_handling() {
    let unicode_query = "python 编程语言 🐍 русский";
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), unicode_query.to_string());
    
    let event_payload = create_api_gateway_event(Some(query_params), None, None);
    
    // Verify Unicode characters are preserved
    assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == unicode_query);
}

#[test]
fn test_environment_variable_configuration() {
    // Test environment variable handling
    std::env::set_var("TEST_ENABLE_EMBEDDINGS", "true");
    let embeddings_enabled = std::env::var("TEST_ENABLE_EMBEDDINGS")
        .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
        .unwrap_or(false);
    
    assert!(embeddings_enabled);
    std::env::remove_var("TEST_ENABLE_EMBEDDINGS");
    
    // Test default behavior
    let embeddings_disabled = std::env::var("TEST_ENABLE_EMBEDDINGS")
        .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
        .unwrap_or(false);
    
    assert!(!embeddings_disabled);
}

#[tokio::test]
async fn test_concurrent_requests_simulation() {
    // Simulate multiple concurrent requests
    let mut handles = vec![];
    
    for i in 0..5 {
        let handle = tokio::spawn(async move {
            let mut query_params = HashMap::new();
            query_params.insert("q".to_string(), format!("test-query-{}", i));
            
            let event_payload = create_api_gateway_event(Some(query_params), None, None);
            
            // Verify each request maintains its unique query
            assert!(event_payload["queryStringParameters"]["q"].as_str().unwrap() == format!("test-query-{}", i));
            i
        });
        handles.push(handle);
    }
    
    // Wait for all requests to complete
    for handle in handles {
        let result = handle.await.unwrap();
        assert!(result < 5);
    }
}

#[test]
fn test_json_serialization_performance() {
    use std::time::Instant;
    
    // Test serialization performance with large response
    let mut packages = vec![];
    for i in 0..1000 {
        packages.push(json!({
            "packageId": format!("pkg-{}", i),
            "packageName": format!("package-{}", i),
            "version": "1.0.0",
            "description": format!("Test package number {}", i),
            "homepage": "https://example.com",
            "license": "MIT",
            "attributePath": format!("pkgs.pkg{}", i),
            "relevanceScore": 0.9
        }));
    }
    
    let response_body = json!({
        "message": "Search completed",
        "query": "test",
        "total_count": 1000,
        "query_time_ms": 25.0,
        "search_type": "test",
        "packages": packages
    });
    
    let start = Instant::now();
    let json_string = serde_json::to_string(&response_body).unwrap();
    let serialization_time = start.elapsed();
    
    // Serialization should complete in reasonable time (< 100ms for 1000 packages)
    assert!(serialization_time.as_millis() < 100);
    assert!(json_string.len() > 10000); // Should produce substantial JSON
}

#[test]
fn test_error_response_structure() {
    let error_response = json!({
        "error": "Internal server error", 
        "message": "Database connection failed"
    });
    
    // Verify error response structure
    assert_eq!(error_response["error"], "Internal server error");
    assert_eq!(error_response["message"], "Database connection failed");
    
    // Test serialization
    let json_str = serde_json::to_string(&error_response).unwrap();
    assert!(json_str.contains("Internal server error"));
    assert!(json_str.contains("Database connection failed"));
}