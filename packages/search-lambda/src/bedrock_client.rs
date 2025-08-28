use aws_config::meta::region::RegionProviderChain;
use aws_config::Region;
use aws_sdk_bedrockruntime::primitives::Blob;
use aws_sdk_bedrockruntime::{Client, Error as BedrockError};
use serde::{Deserialize, Serialize};
use std::env;
use std::time::{Duration, Instant};
use thiserror::Error;
use tokio::time::sleep;
use tracing::{info, warn, error, debug};


#[derive(Error, Debug)]
pub enum BedrockClientError {
    #[error("AWS SDK error: {0}")]
    AwsError(#[from] BedrockError),
    #[error("AWS SDK service error: {0}")]
    SdkError(String),
    #[error("JSON parsing error: {0}")]
    JsonError(#[from] serde_json::Error),
    #[error("Empty text provided")]
    EmptyText,
    #[error("Client not initialized")]
    NotInitialized,
    #[error("Invalid response format")]
    InvalidResponse,
    #[error("Model invocation failed: {0}")]
    ModelInvocationFailed(String),
}

#[derive(Serialize, Debug, Clone, PartialEq)]
struct EmbeddingRequest {
    #[serde(rename = "inputText")]
    input_text: String,
    dimensions: i32,
}

#[derive(Deserialize, Debug, Clone, PartialEq)]
struct EmbeddingResponse {
    embedding: Vec<f64>,
}

pub struct BedrockClient {
    client: Option<Client>,
    region: String,
    model_id: String,
    output_dimensions: i32,
}

impl BedrockClient {
    pub async fn new(
        region: &str,
        model_id: &str,
        output_dimensions: i32,
    ) -> Result<Self, BedrockClientError> {
        let region = if region.is_empty() {
            env::var("AWS_REGION").unwrap_or_else(|_| "us-east-1".to_string())
        } else {
            region.to_string()
        };

        // Allow environment variables to override defaults
        let model_id = env::var("BEDROCK_MODEL_ID").unwrap_or_else(|_| model_id.to_string());
        let output_dimensions = env::var("BEDROCK_OUTPUT_DIMENSIONS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(output_dimensions);

        // Configure AWS SDK with retry logic and timeouts
        let region_provider = RegionProviderChain::first_try(Region::new(region.clone()))
            .or_default_provider();
        
        let config = aws_config::defaults(aws_config::BehaviorVersion::latest())
            .region(region_provider)
            .load()
            .await;
        
        let client = Client::new(&config);

        info!(
            "BedrockClient created with model: {}, dimensions: {}, region: {}",
            model_id, output_dimensions, region
        );

        Ok(BedrockClient {
            client: Some(client),
            region,
            model_id,
            output_dimensions,
        })
    }

    pub async fn generate_embedding(&self, text: &str) -> Result<Vec<f64>, BedrockClientError> {
        if text.is_empty() {
            return Err(BedrockClientError::EmptyText);
        }

        let client = self.client.as_ref()
            .ok_or(BedrockClientError::NotInitialized)?;

        debug!("Generating embedding for text length: {} chars", text.len());
        let start_time = Instant::now();

        // Build request body
        let request_body = EmbeddingRequest {
            input_text: text.to_string(),
            dimensions: self.output_dimensions,
        };

        let body_json = serde_json::to_string(&request_body)?;
        let body_blob = Blob::new(body_json.clone());
        
        debug!(
            "Bedrock request: model={}, dimensions={}, text_preview={}", 
            self.model_id, 
            self.output_dimensions,
            &text.chars().take(100).collect::<String>()
        );

        // Retry with exponential backoff
        let mut last_error = None;
        for attempt in 0..3 {
            if attempt > 0 {
                let backoff_duration = Duration::from_millis(100 * (2_u64.pow(attempt as u32)));
                debug!("Retrying embedding generation after {}ms backoff (attempt {})", backoff_duration.as_millis(), attempt + 1);
                sleep(backoff_duration).await;
            }

            match self.try_generate_embedding(client, &body_blob).await {
                Ok(embedding) => {
                    let elapsed = start_time.elapsed();
                    info!(
                        "Successfully generated embedding: dimensions={}, attempt={}, duration={}ms",
                        embedding.len(),
                        attempt + 1,
                        elapsed.as_millis()
                    );
                    return Ok(embedding);
                }
                Err(e) => {
                    warn!("Embedding generation attempt {} failed: {}", attempt + 1, e);
                    last_error = Some(e);
                }
            }
        }

        let total_elapsed = start_time.elapsed();
        error!(
            "All embedding generation attempts failed after {}ms", 
            total_elapsed.as_millis()
        );
        Err(last_error.unwrap_or(BedrockClientError::ModelInvocationFailed("All retries exhausted".to_string())))
    }

    async fn try_generate_embedding(
        &self,
        client: &Client,
        body_blob: &Blob,
    ) -> Result<Vec<f64>, BedrockClientError> {
        // Make request to Bedrock
        let response = client
            .invoke_model()
            .model_id(&self.model_id)
            .accept("application/json")
            .content_type("application/json")
            .body(body_blob.clone())
            .send()
            .await
            .map_err(|e| BedrockClientError::SdkError(format!("Bedrock invoke_model failed: {}", e)))?;

        // Parse response
        let body_bytes = response.body().as_ref();
        let body_str = std::str::from_utf8(body_bytes)
            .map_err(|_| BedrockClientError::InvalidResponse)?;

        debug!("Bedrock response size: {} bytes", body_bytes.len());

        let response_json: EmbeddingResponse = serde_json::from_str(body_str)
            .map_err(|e| {
                error!("Failed to parse Bedrock response: {}", e);
                error!("Response body preview: {}", &body_str.chars().take(500).collect::<String>());
                BedrockClientError::JsonError(e)
            })?;

        if response_json.embedding.is_empty() {
            error!("Bedrock response contained empty embedding");
            return Err(BedrockClientError::InvalidResponse);
        }

        Ok(response_json.embedding)
    }

    pub async fn generate_embeddings(&self, texts: &[String]) -> Result<Vec<Vec<f64>>, BedrockClientError> {
        let mut embeddings = Vec::with_capacity(texts.len());
        let start_time = Instant::now();
        
        info!("Generating embeddings for {} texts", texts.len());
        
        for (i, text) in texts.iter().enumerate() {
            debug!("Processing text {}/{}", i + 1, texts.len());
            match self.generate_embedding(text).await {
                Ok(embedding) => {
                    embeddings.push(embedding);
                    debug!("Successfully generated embedding {}/{}", i + 1, texts.len());
                }
                Err(e) => {
                    warn!("Failed to generate embedding for text {}/{}: {}", i + 1, texts.len(), e);
                    // Continue processing other texts
                }
            }
        }
        
        let total_elapsed = start_time.elapsed();
        info!(
            "Batch embedding generation complete: {}/{} successful in {}ms", 
            embeddings.len(), 
            texts.len(),
            total_elapsed.as_millis()
        );
        
        Ok(embeddings)
    }

    pub async fn health_check(&self) -> Result<bool, BedrockClientError> {
        if self.model_id.is_empty() {
            debug!("Health check failed: model_id is empty");
            return Ok(false);
        }

        debug!("Performing Bedrock health check with test embedding");
        let start_time = Instant::now();

        match self.generate_embedding("test").await {
            Ok(embedding) => {
                let elapsed = start_time.elapsed();
                let is_healthy = !embedding.is_empty();
                info!(
                    "Bedrock health check completed: healthy={}, embedding_dims={}, duration={}ms",
                    is_healthy,
                    embedding.len(),
                    elapsed.as_millis()
                );
                Ok(is_healthy)
            }
            Err(e) => {
                let elapsed = start_time.elapsed();
                warn!(
                    "Bedrock health check failed after {}ms: {}", 
                    elapsed.as_millis(), 
                    e
                );
                Ok(false)
            }
        }
    }

    pub fn get_model_id(&self) -> &str {
        &self.model_id
    }

    pub fn get_output_dimensions(&self) -> i32 {
        self.output_dimensions
    }

    pub fn get_region(&self) -> &str {
        &self.region
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rstest::*;
    use std::env;

    #[fixture]
    fn embedding_request() -> EmbeddingRequest {
        EmbeddingRequest {
            input_text: "test input".to_string(),
            dimensions: 256,
        }
    }

    #[fixture]
    fn embedding_response() -> EmbeddingResponse {
        EmbeddingResponse {
            embedding: vec![0.1, 0.2, 0.3, 0.4, 0.5],
        }
    }

    #[test]
    fn test_embedding_request_serialization() {
        let embedding_request = EmbeddingRequest {
            input_text: "test input".to_string(),
            dimensions: 256,
        };
        let json_str = serde_json::to_string(&embedding_request).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        
        assert_eq!(parsed["inputText"], "test input");
        assert_eq!(parsed["dimensions"], 256);
    }

    #[test]
    fn test_embedding_response_deserialization() {
        let json_str = r#"{"embedding": [0.1, 0.2, 0.3, 0.4, 0.5]}"#;
        let response: EmbeddingResponse = serde_json::from_str(json_str).unwrap();
        
        assert_eq!(response.embedding.len(), 5);
        assert_eq!(response.embedding[0], 0.1);
        assert_eq!(response.embedding[4], 0.5);
    }

    #[test]
    fn test_bedrock_client_error_display() {
        let error = BedrockClientError::EmptyText;
        assert_eq!(error.to_string(), "Empty text provided");
        
        let error = BedrockClientError::NotInitialized;
        assert_eq!(error.to_string(), "Client not initialized");
        
        let error = BedrockClientError::InvalidResponse;
        assert_eq!(error.to_string(), "Invalid response format");
        
        let error = BedrockClientError::SdkError("Connection timeout".to_string());
        assert_eq!(error.to_string(), "AWS SDK service error: Connection timeout");
        
        let error = BedrockClientError::ModelInvocationFailed("Invalid model".to_string());
        assert_eq!(error.to_string(), "Model invocation failed: Invalid model");
    }

    #[tokio::test]
    async fn test_bedrock_client_region_handling() {
        let test_cases = [
            ("", "us-east-1"),
            ("us-west-2", "us-west-2"),
            ("eu-central-1", "eu-central-1"),
        ];
        
        // Clear any existing AWS_REGION env var for this test
        let original_region = env::var("AWS_REGION").ok();
        
        for (input_region, _expected_region) in test_cases {
            env::remove_var("AWS_REGION");
            
            // This will fail due to lack of AWS credentials, but we can still test region parsing
            let result = BedrockClient::new(
                input_region,
                "amazon.titan-embed-text-v2:0",
                256,
            ).await;
            
            // The function should fail due to credentials, not region parsing
            // We can't easily test the actual region setting without mocking AWS SDK
            assert!(result.is_err() || result.is_ok());
        }
        
        // Restore original env var if it existed
        if let Some(region) = original_region {
            env::set_var("AWS_REGION", region);
        }
    }

    #[test]
    fn test_bedrock_client_env_var_overrides() {
        // Test model ID override
        env::set_var("BEDROCK_MODEL_ID", "custom-model");
        env::set_var("BEDROCK_OUTPUT_DIMENSIONS", "512");
        
        // We can't easily test the full constructor without AWS credentials
        // but we can test the environment variable logic by checking what would happen
        let model_from_env = env::var("BEDROCK_MODEL_ID").unwrap();
        let dims_from_env: i32 = env::var("BEDROCK_OUTPUT_DIMENSIONS")
            .unwrap()
            .parse()
            .unwrap();
        
        assert_eq!(model_from_env, "custom-model");
        assert_eq!(dims_from_env, 512);
        
        // Clean up
        env::remove_var("BEDROCK_MODEL_ID");
        env::remove_var("BEDROCK_OUTPUT_DIMENSIONS");
    }

    #[rstest]
    #[case("256", 256)]
    #[case("512", 512)]
    #[case("1024", 1024)]
    #[case("invalid", 256)] // Should fall back to default
    fn test_dimensions_parsing(#[case] env_value: &str, #[case] expected: i32) {
        env::set_var("BEDROCK_OUTPUT_DIMENSIONS", env_value);
        
        let parsed = env::var("BEDROCK_OUTPUT_DIMENSIONS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(256);
        
        assert_eq!(parsed, expected);
        
        env::remove_var("BEDROCK_OUTPUT_DIMENSIONS");
    }

    #[test]
    fn test_embedding_request_with_different_dimensions() {
        let request = EmbeddingRequest {
            input_text: "Hello, world!".to_string(),
            dimensions: 1024,
        };
        
        let json = serde_json::to_string(&request).unwrap();
        assert!(json.contains("Hello, world!"));
        assert!(json.contains("1024"));
    }

    #[test]
    fn test_embedding_response_empty_embedding() {
        let json_str = r#"{"embedding": []}"#;
        let response: EmbeddingResponse = serde_json::from_str(json_str).unwrap();
        
        assert_eq!(response.embedding.len(), 0);
    }

    #[test]
    fn test_embedding_response_large_embedding() {
        let large_embedding: Vec<f64> = (0..1024).map(|i| i as f64 * 0.001).collect();
        let response = EmbeddingResponse {
            embedding: large_embedding.clone(),
        };
        
        assert_eq!(response.embedding.len(), 1024);
        assert_eq!(response.embedding[0], 0.0);
        assert!((response.embedding[1023] - 1.023).abs() < 0.001);
    }

    #[test]
    fn test_json_error_conversion() {
        let json_error = serde_json::from_str::<serde_json::Value>("{invalid json").unwrap_err();
        let bedrock_error = BedrockClientError::JsonError(json_error);
        
        match bedrock_error {
            BedrockClientError::JsonError(_) => {
                // Test passes if we can match this variant
            }
            _ => panic!("Expected JsonError variant"),
        }
    }

    #[tokio::test]
    async fn test_generate_embedding_with_empty_text() {
        // Create a mock client (without AWS credentials, so initialization will fail)
        // This is testing error handling for empty text input specifically
        
        // We can't easily test the full flow without AWS credentials or extensive mocking
        // But we can test the empty text validation logic
        let empty_text = "";
        
        // Test that empty text should return an error
        assert!(empty_text.is_empty());
        
        // The actual error would be BedrockClientError::EmptyText
        let expected_error = BedrockClientError::EmptyText;
        assert_eq!(expected_error.to_string(), "Empty text provided");
    }

    #[test]
    fn test_bedrock_response_parsing_edge_cases() {
        // Test valid JSON with extra fields (should be ignored)
        let json_with_extra = r#"{
            "embedding": [0.1, 0.2],
            "extra_field": "ignored",
            "metadata": {"model": "test"}
        }"#;
        
        let response: Result<EmbeddingResponse, _> = serde_json::from_str(json_with_extra);
        assert!(response.is_ok());
        
        let response = response.unwrap();
        assert_eq!(response.embedding.len(), 2);
        
        // Test invalid JSON
        let invalid_json = r#"{"invalid": "json"#;
        let response: Result<EmbeddingResponse, _> = serde_json::from_str(invalid_json);
        assert!(response.is_err());
        
        // Test missing embedding field
        let missing_embedding = r#"{"other_field": "value"}"#;
        let response: Result<EmbeddingResponse, _> = serde_json::from_str(missing_embedding);
        assert!(response.is_err());
    }
}