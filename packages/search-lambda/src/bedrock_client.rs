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

#[derive(Serialize)]
struct EmbeddingRequest {
    #[serde(rename = "inputText")]
    input_text: String,
    dimensions: i32,
}

#[derive(Deserialize)]
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