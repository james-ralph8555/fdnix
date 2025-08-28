use serde_json::{json, Value};
use std::collections::HashMap;

/// Sample API Gateway event for search requests
pub fn sample_search_event() -> Value {
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), "nodejs".to_string());
    query_params.insert("limit".to_string(), "10".to_string());
    query_params.insert("offset".to_string(), "0".to_string());

    json!({
        "resource": "/search",
        "path": "/search", 
        "httpMethod": "GET",
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "test-client/1.0"
        },
        "multiValueHeaders": {},
        "queryStringParameters": query_params,
        "multiValueQueryStringParameters": {},
        "pathParameters": null,
        "stageVariables": null,
        "requestContext": {
            "requestId": "test-search-request-123",
            "stage": "test",
            "resourcePath": "/search",
            "httpMethod": "GET",
            "path": "/test/search",
            "protocol": "HTTP/1.1",
            "requestTime": "27/Aug/2025:12:00:00 +0000",
            "requestTimeEpoch": 1724760000,
            "identity": {
                "sourceIp": "127.0.0.1",
                "userAgent": "test-client/1.0"
            }
        },
        "body": null,
        "isBase64Encoded": false
    })
}

/// Health check API Gateway event (no query parameters)
pub fn health_check_event() -> Value {
    json!({
        "resource": "/search",
        "path": "/search",
        "httpMethod": "GET",
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        "multiValueHeaders": {},
        "queryStringParameters": null,
        "multiValueQueryStringParameters": {},
        "pathParameters": null,
        "stageVariables": null,
        "requestContext": {
            "requestId": "test-health-request-456",
            "stage": "test",
            "resourcePath": "/search",
            "httpMethod": "GET",
            "path": "/test/search",
            "protocol": "HTTP/1.1",
            "requestTime": "27/Aug/2025:12:00:00 +0000",
            "requestTimeEpoch": 1724760000
        },
        "body": null,
        "isBase64Encoded": false
    })
}

/// API Gateway event with filters and pagination
pub fn filtered_search_event() -> Value {
    let mut query_params = HashMap::new();
    query_params.insert("q".to_string(), "python web framework".to_string());
    query_params.insert("limit".to_string(), "25".to_string());
    query_params.insert("offset".to_string(), "50".to_string());
    query_params.insert("license".to_string(), "MIT".to_string());
    query_params.insert("category".to_string(), "development".to_string());

    json!({
        "resource": "/search",
        "path": "/search",
        "httpMethod": "GET", 
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        "multiValueHeaders": {},
        "queryStringParameters": query_params,
        "multiValueQueryStringParameters": {},
        "pathParameters": null,
        "stageVariables": null,
        "requestContext": {
            "requestId": "test-filtered-request-789",
            "stage": "test",
            "resourcePath": "/search",
            "httpMethod": "GET",
            "path": "/test/search",
            "protocol": "HTTP/1.1",
            "requestTime": "27/Aug/2025:12:00:00 +0000",
            "requestTimeEpoch": 1724760000
        },
        "body": null,
        "isBase64Encoded": false
    })
}

/// Malformed API Gateway event for error testing
pub fn malformed_event() -> Value {
    json!({
        "invalidField": "this is not a valid API Gateway event",
        "randomData": 12345,
        "nestedObject": {
            "key": "value"
        }
    })
}

/// Sample package data for testing
pub fn sample_packages() -> Vec<Value> {
    vec![
        json!({
            "packageId": "nodejs-18.17.1",
            "packageName": "nodejs",
            "version": "18.17.1",
            "description": "Event-driven I/O framework for the V8 JavaScript engine",
            "homepage": "https://nodejs.org",
            "license": "MIT",
            "attributePath": "pkgs.nodejs",
            "relevanceScore": 0.95
        }),
        json!({
            "packageId": "python3-3.11.4",
            "packageName": "python3",
            "version": "3.11.4", 
            "description": "A high-level dynamically-typed programming language",
            "homepage": "https://python.org",
            "license": "PSF-2.0",
            "attributePath": "pkgs.python3",
            "relevanceScore": 0.92
        }),
        json!({
            "packageId": "rustc-1.71.0",
            "packageName": "rustc",
            "version": "1.71.0",
            "description": "A safe, concurrent, practical language",
            "homepage": "https://www.rust-lang.org/",
            "license": "Apache-2.0 OR MIT",
            "attributePath": "pkgs.rustc",
            "relevanceScore": 0.88
        }),
        json!({
            "packageId": "go-1.20.6",
            "packageName": "go",
            "version": "1.20.6",
            "description": "The Go Programming Language",
            "homepage": "https://go.dev/",
            "license": "BSD-3-Clause",
            "attributePath": "pkgs.go",
            "relevanceScore": 0.85
        }),
        json!({
            "packageId": "gcc-12.3.0",
            "packageName": "gcc",
            "version": "12.3.0",
            "description": "GNU Compiler Collection",
            "homepage": "https://gcc.gnu.org/",
            "license": "GPL-3.0-or-later",
            "attributePath": "pkgs.gcc",
            "relevanceScore": 0.82
        })
    ]
}

/// Sample successful search response
pub fn sample_search_response() -> Value {
    json!({
        "message": "Search completed",
        "query": "nodejs",
        "total_count": 1,
        "query_time_ms": 25.4,
        "search_type": "hybrid",
        "packages": [sample_packages()[0].clone()]
    })
}

/// Sample health check response
pub fn sample_health_response() -> Value {
    json!({
        "message": "fdnix search API (Rust) — stub active",
        "note": "This is a Rust Lambda stub. LanceDB integration ready.",
        "version": "0.1.0",
        "runtime": "provided.al2023",
        "lancedb_path": "/opt/packages.lance",
        "bedrock_model_id": "amazon.titan-embed-text-v2:0",
        "aws_region": "us-east-1",
        "enable_embeddings": "true",
        "lancedb_initialized": true,
        "bedrock_initialized": true,
        "lancedb_healthy": true,
        "bedrock_healthy": true
    })
}

/// Sample error response
pub fn sample_error_response() -> Value {
    json!({
        "error": "Internal server error",
        "message": "Database connection failed"
    })
}

/// Large dataset for performance testing
pub fn large_package_dataset(count: usize) -> Vec<Value> {
    let mut packages = Vec::with_capacity(count);
    let languages = vec!["rust", "python", "nodejs", "go", "java", "cpp", "javascript", "typescript"];
    let licenses = vec!["MIT", "Apache-2.0", "GPL-3.0", "BSD-3-Clause", "ISC", "LGPL-2.1"];
    let categories = vec!["development", "system", "network", "database", "web", "cli", "library"];
    
    for i in 0..count {
        let lang = &languages[i % languages.len()];
        let license = &licenses[i % licenses.len()];
        let category = &categories[i % categories.len()];
        
        packages.push(json!({
            "packageId": format!("{}-{}-{}", lang, category, i),
            "packageName": format!("{}-package-{}", lang, i),
            "version": format!("1.{}.{}", i % 100, i % 10),
            "description": format!("A {} package for {} development (package #{})", lang, category, i),
            "homepage": format!("https://{}-package-{}.example.com", lang, i),
            "license": license,
            "attributePath": format!("pkgs.{}.package{}", category, i),
            "relevanceScore": (100 - (i % 100)) as f64 / 100.0
        }));
    }
    
    packages
}

/// Mock Bedrock embedding response
pub fn mock_bedrock_response() -> Value {
    json!({
        "embedding": [
            0.123, 0.456, 0.789, 0.012, 0.345, 0.678, 0.901, 0.234,
            0.567, 0.890, 0.123, 0.456, 0.789, 0.012, 0.345, 0.678,
            0.901, 0.234, 0.567, 0.890, 0.123, 0.456, 0.789, 0.012,
            0.345, 0.678, 0.901, 0.234, 0.567, 0.890, 0.123, 0.456
        ]
    })
}

/// Environment variables for testing
pub fn setup_test_environment() {
    std::env::set_var("LANCEDB_PATH", "/tmp/test-packages.lance");
    std::env::set_var("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0");
    std::env::set_var("AWS_REGION", "us-east-1");
    std::env::set_var("ENABLE_EMBEDDINGS", "true");
    std::env::set_var("BEDROCK_OUTPUT_DIMENSIONS", "256");
}

/// Clean up test environment
pub fn cleanup_test_environment() {
    std::env::remove_var("LANCEDB_PATH");
    std::env::remove_var("BEDROCK_MODEL_ID");
    std::env::remove_var("AWS_REGION");
    std::env::remove_var("ENABLE_EMBEDDINGS");
    std::env::remove_var("BEDROCK_OUTPUT_DIMENSIONS");
}

/// Edge case query strings for testing
pub fn edge_case_queries() -> Vec<String> {
    vec![
        "".to_string(),                                    // Empty query
        " ".to_string(),                                   // Whitespace only
        "a".to_string(),                                   // Single character
        "a".repeat(1000),                                  // Very long query
        "special!@#$%^&*()chars".to_string(),             // Special characters
        "unicode: 🦀 русский 中文 العربية".to_string(),     // Unicode
        "SQL injection'; DROP TABLE packages;--".to_string(), // Security test
        "nested\"quotes'and`backticks".to_string(),       // Quote variations
        "\n\r\t\x00".to_string(),                         // Control characters
        "c++/c# .net framework".to_string(),              // Programming languages
        "package-with-dashes_and_underscores".to_string(), // Common separators
        "UPPERCASE lowercase MiXeD CaSe".to_string(),      // Case variations
    ]
}

/// Mock database connection errors
pub fn database_error_scenarios() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Connection timeout", "Database connection timed out after 30 seconds"),
        ("Table not found", "Required table 'packages' not found in database"),
        ("Permission denied", "Insufficient permissions to access database"),
        ("Disk full", "No space left on device for database operations"),
        ("Corrupted index", "Database index is corrupted and needs rebuilding"),
        ("Lock timeout", "Could not acquire database lock within timeout period"),
        ("Invalid path", "Database path does not exist or is not accessible"),
        ("Schema mismatch", "Database schema version is incompatible"),
    ]
}

/// Mock AWS/Bedrock errors
pub fn bedrock_error_scenarios() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Throttling", "Rate limit exceeded for model invocations"),
        ("Model not found", "Specified model ID does not exist or is not available"),
        ("Authentication", "AWS credentials are invalid or expired"),
        ("Region unavailable", "Bedrock service is not available in specified region"),
        ("Input too large", "Input text exceeds maximum token limit for model"),
        ("Service unavailable", "Bedrock service is temporarily unavailable"),
        ("Quota exceeded", "Monthly usage quota has been exceeded"),
        ("Invalid request", "Request format is invalid or missing required fields"),
    ]
}

/// Performance test scenarios
pub struct PerformanceTestCase {
    pub name: &'static str,
    pub query: String,
    pub expected_max_time_ms: u64,
    pub package_count: usize,
}

pub fn performance_test_cases() -> Vec<PerformanceTestCase> {
    vec![
        PerformanceTestCase {
            name: "small_result_set",
            query: "very_specific_package_name".to_string(),
            expected_max_time_ms: 50,
            package_count: 1,
        },
        PerformanceTestCase {
            name: "medium_result_set", 
            query: "python".to_string(),
            expected_max_time_ms: 100,
            package_count: 50,
        },
        PerformanceTestCase {
            name: "large_result_set",
            query: "library".to_string(),
            expected_max_time_ms: 200,
            package_count: 500,
        },
        PerformanceTestCase {
            name: "very_long_query",
            query: "programming language framework library utility tool ".repeat(20),
            expected_max_time_ms: 150,
            package_count: 100,
        },
    ]
}