use lancedb::{Connection, Table};
use lancedb::query::{QueryBase, ExecutableQuery, Select};
use lance_index::scalar::FullTextSearchQuery;
use arrow::array::{RecordBatch, StringArray};
use futures_util::stream::TryStreamExt;
use serde::{Deserialize, Serialize};
use std::env;
use std::time::Instant;
use thiserror::Error;
use tracing::{info, warn, error};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Package {
    pub package_id: String,
    pub package_name: String,
    pub version: String,
    pub description: String,
    pub homepage: String,
    pub license: String,
    pub attribute_path: String,
    pub relevance_score: f64,
}

#[derive(Debug, Clone)]
pub struct SearchParams {
    pub query: String,
    pub limit: i32,
    pub offset: i32,
    pub license_filter: Option<String>,
    pub category_filter: Option<String>,
}

#[derive(Debug, Clone)]
pub struct SearchResults {
    pub packages: Vec<Package>,
    pub total_count: i32,
    pub query_time_ms: f64,
    pub search_type: String,
}

#[derive(Error, Debug)]
pub enum LanceDBClientError {
    #[error("Database error: {0}")]
    DatabaseError(String),
    #[error("Connection not initialized")]
    NotInitialized,
    #[error("Required table not found: {0}")]
    TableNotFound(String),
    #[error("Query failed: {0}")]
    QueryFailed(String),
    #[error("Arrow error: {0}")]
    ArrowError(#[from] arrow::error::ArrowError),
    #[error("LanceDB error: {0}")]
    LanceDBError(#[from] lancedb::Error),
}

pub struct LanceDBClient {
    db_path: String,
    connection: Option<Connection>,
    table: Option<Table>,
    embeddings_enabled: bool,
}

unsafe impl Send for LanceDBClient {}
unsafe impl Sync for LanceDBClient {}

impl LanceDBClient {
    pub fn new(db_path: &str) -> Result<Self, LanceDBClientError> {
        let embeddings_enabled = env::var("ENABLE_EMBEDDINGS")
            .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
            .unwrap_or(false);

        info!(
            "LanceDBClient created for database: {}, embeddings enabled: {}",
            db_path, embeddings_enabled
        );

        Ok(LanceDBClient {
            db_path: db_path.to_string(),
            connection: None,
            table: None,
            embeddings_enabled,
        })
    }

    pub async fn initialize(&mut self) -> Result<bool, LanceDBClientError> {
        let conn = lancedb::connect(&self.db_path).execute().await?;

        // Check if packages table exists
        let table_names = conn.table_names().execute().await?;
        if !table_names.contains(&"packages".to_string()) {
            error!("Required 'packages' table not found in LanceDB");
            return Err(LanceDBClientError::TableNotFound("packages".to_string()));
        }

        let table = conn.open_table("packages").execute().await?;
        info!("Found required 'packages' table");

        // Check embeddings availability if enabled
        if self.embeddings_enabled {
            if self.check_embeddings_availability(&table).await {
                info!("Embeddings available and enabled");
            } else {
                warn!("No embeddings found in packages table - disabling embeddings, using FTS-only mode");
                self.embeddings_enabled = false;
            }
        }

        self.connection = Some(conn);
        self.table = Some(table);

        info!(
            "LanceDB client initialized successfully (embeddings: {})",
            if self.embeddings_enabled { "enabled" } else { "disabled" }
        );

        Ok(true)
    }

    pub async fn hybrid_search(
        &self,
        params: &SearchParams,
        query_embedding: Option<&[f64]>,
    ) -> Result<SearchResults, LanceDBClientError> {
        let start_time = Instant::now();
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "unknown".to_string(),
        };

        let table = self.table.as_ref()
            .ok_or(LanceDBClientError::NotInitialized)?;

        if self.embeddings_enabled && query_embedding.is_some() && !query_embedding.unwrap().is_empty() {
            // Hybrid search mode using LanceDB's built-in hybrid search
            results.search_type = "hybrid".to_string();
            
            let query_vec: Vec<f32> = query_embedding.unwrap().iter().map(|&x| x as f32).collect();
            
            let search_results = table
                .query()
                .nearest_to(query_vec)?
                .limit(params.limit as usize)
                .execute()
                .await?;

            let batches: Vec<RecordBatch> = search_results.try_collect().await?;
            results.packages = self.arrow_batches_to_packages(batches)?;
        } else {
            // FTS-only search mode
            results.search_type = "fts".to_string();
            let fts_results = self.fts_search(&params.query, params.limit).await?;
            results.packages = fts_results.packages;
        }

        // Apply filters
        if params.license_filter.is_some() || params.category_filter.is_some() {
            results.packages.retain(|pkg| {
                if let Some(license_filter) = &params.license_filter {
                    if !pkg.license.contains(license_filter) {
                        return false;
                    }
                }
                true
            });
        }

        // Apply offset and limit
        if params.offset > 0 && (params.offset as usize) < results.packages.len() {
            results.packages.drain(0..(params.offset as usize));
        }

        if params.limit > 0 && results.packages.len() > (params.limit as usize) {
            results.packages.truncate(params.limit as usize);
        }

        results.total_count = results.packages.len() as i32;
        results.query_time_ms = start_time.elapsed().as_millis() as f64;

        Ok(results)
    }

    pub async fn vector_search(&self, query_embedding: &[f64], limit: i32) -> Result<SearchResults, LanceDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "vector".to_string(),
        };

        let table = self.table.as_ref()
            .ok_or(LanceDBClientError::NotInitialized)?;

        if !self.embeddings_enabled || query_embedding.is_empty() {
            return Ok(results);
        }

        let query_vec: Vec<f32> = query_embedding.iter().map(|&x| x as f32).collect();
        
        let search_results = table
            .query()
            .nearest_to(query_vec)?
            .limit(limit as usize)
            .execute()
            .await?;

        let batches: Vec<RecordBatch> = search_results.try_collect().await?;
        results.packages = self.arrow_batches_to_packages(batches)?;
        results.total_count = results.packages.len() as i32;

        Ok(results)
    }

    pub async fn fts_search(&self, query: &str, limit: i32) -> Result<SearchResults, LanceDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "fts".to_string(),
        };

        let table = self.table.as_ref()
            .ok_or(LanceDBClientError::NotInitialized)?;

        if query.is_empty() {
            return Ok(results);
        }

        let search_results = table
            .query()
            .full_text_search(FullTextSearchQuery::new(query.to_owned()))
            .select(Select::All)
            .limit(limit as usize)
            .execute()
            .await
            .map_err(|e| {
                error!("FTS search failed: {}, falling back to filter search", e);
                e
            });

        match search_results {
            Ok(stream) => {
                let batches: Vec<RecordBatch> = stream.try_collect().await?;
                results.packages = self.arrow_batches_to_packages(batches)?;
            }
            Err(_) => {
                // Fallback to basic filter search
                let fallback_results = self.fallback_search(query, limit).await?;
                results.packages = fallback_results.packages;
            }
        }

        results.total_count = results.packages.len() as i32;
        Ok(results)
    }

    async fn fallback_search(&self, query: &str, limit: i32) -> Result<SearchResults, LanceDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "fallback".to_string(),
        };

        let table = self.table.as_ref()
            .ok_or(LanceDBClientError::NotInitialized)?;

        // Use basic filter for fallback
        let filter = format!("package_name LIKE '%{}%' OR description LIKE '%{}%'", query, query);
        
        let search_results = table
            .query()
            .only_if(&filter)
            .limit(limit as usize)
            .execute()
            .await?;

        let batches: Vec<RecordBatch> = search_results.try_collect().await?;
        results.packages = self.arrow_batches_to_packages(batches)?;
        
        // Assign decreasing scores for fallback results
        for (i, pkg) in results.packages.iter_mut().enumerate() {
            pkg.relevance_score = 1.0 - (i as f64 * 0.1);
        }

        results.total_count = results.packages.len() as i32;
        Ok(results)
    }

    pub async fn health_check(&self) -> bool {
        match &self.table {
            Some(table) => {
                match table.count_rows(None).await {
                    Ok(_) => true,
                    Err(e) => {
                        error!("Health check failed: {}", e);
                        false
                    }
                }
            }
            None => false,
        }
    }

    async fn check_embeddings_availability(&self, table: &Table) -> bool {
        // Check if vector column exists and has data
        match table.query().limit(1).execute().await {
            Ok(mut stream) => {
                if let Ok(Some(batch)) = stream.try_next().await {
                    let schema = batch.schema();
                    let has_vector_col = schema.fields().iter().any(|field| field.name() == "vector");
                
                    if !has_vector_col {
                        return false;
                    }

                    // Check if we have any non-null vectors
                    match table.count_rows(Some("vector IS NOT NULL".to_string())).await {
                        Ok(count) => count > 0,
                        Err(_) => false,
                    }
                } else {
                    false
                }
            }
            Err(_) => false,
        }
    }

    fn arrow_batches_to_packages(&self, batches: Vec<RecordBatch>) -> Result<Vec<Package>, LanceDBClientError> {
        let mut all_packages = Vec::new();
        for batch in batches {
            let mut packages = self.arrow_to_packages(batch)?;
            all_packages.append(&mut packages);
        }
        Ok(all_packages)
    }

    fn arrow_to_packages(&self, batch: RecordBatch) -> Result<Vec<Package>, LanceDBClientError> {
        let mut packages = Vec::new();
        let num_rows = batch.num_rows();
        
        // Get column arrays
        let package_id_col = batch.column_by_name("package_id")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let package_name_col = batch.column_by_name("package_name")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let version_col = batch.column_by_name("version")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let description_col = batch.column_by_name("description")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let homepage_col = batch.column_by_name("homepage")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let license_col = batch.column_by_name("license")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        let attribute_path_col = batch.column_by_name("attribute_path")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());

        for i in 0..num_rows {
            let package = Package {
                package_id: package_id_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                package_name: package_name_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                version: version_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                description: description_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                homepage: homepage_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                license: license_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                attribute_path: attribute_path_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                relevance_score: 1.0, // Will be computed based on search results
            };
            packages.push(package);
        }

        Ok(packages)
    }
}