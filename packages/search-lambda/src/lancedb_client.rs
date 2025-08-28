use lancedb::{Connection, Table, DistanceType};
use lancedb::query::{QueryBase, ExecutableQuery, Select, QueryExecutionOptions};
use lance_index::scalar::FullTextSearchQuery;
use arrow::array::{Array, RecordBatch, StringArray, Float64Array, Float32Array};
use futures_util::stream::TryStreamExt;
use serde::{Deserialize, Serialize};
use std::env;
use std::time::Instant;
use thiserror::Error;
use tracing::{info, warn, error};


#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Package {
    pub package_id: String,
    pub package_name: String,
    pub version: String,
    pub description: String,
    pub homepage: String,
    pub license: String,
    pub attribute_path: String,
    pub category: String,
    pub broken: bool,
    pub unfree: bool,
    pub available: bool,
    pub relevance_score: f64,
}

#[derive(Debug, Clone)]
pub struct SearchParams {
    pub query: String,
    pub limit: i32,
    pub offset: i32,
    pub license_filter: Option<String>,
    pub category_filter: Option<String>,
    pub include_broken: bool,
    pub include_unfree: bool,
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

        // Debug: List actual directory contents
        if let Ok(entries) = std::fs::read_dir(&self.db_path) {
            let mut dir_contents = Vec::new();
            for entry in entries {
                if let Ok(entry) = entry {
                    dir_contents.push(entry.file_name().to_string_lossy().to_string());
                }
            }
            info!("Database directory contents: {:?}", dir_contents);
        }

        // Check if packages table exists
        let table_names = conn.table_names().execute().await?;
        info!("Available tables in LanceDB: {:?}", table_names);
        
        if table_names.is_empty() {
            error!("No tables found in LanceDB database at {}", self.db_path);
            error!("This suggests the database was not properly created or populated");
            
            // Try to provide more debugging information
            error!("Database appears to have data files but no registered tables");
            error!("This could indicate a version mismatch or incomplete database creation");
            
            return Err(LanceDBClientError::TableNotFound("no tables found in database".to_string()));
        }
        
        // Try to find packages table or use the first available table
        let table_name = if table_names.contains(&"packages".to_string()) {
            "packages".to_string()
        } else if !table_names.is_empty() {
            warn!("'packages' table not found, trying to use first available table: {}", table_names[0]);
            table_names[0].clone()
        } else {
            error!("Required 'packages' table not found in LanceDB");
            return Err(LanceDBClientError::TableNotFound("packages".to_string()));
        };

        let table = conn.open_table(&table_name).execute().await?;
        info!("Successfully opened table: {}", table_name);

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
            // True hybrid search mode using LanceDB's built-in hybrid search
            results.search_type = "hybrid".to_string();
            
            let query_vec: Vec<f32> = query_embedding.unwrap().iter().map(|&x| x as f32).collect();
            
            let search_results = table
                .query()
                .full_text_search(FullTextSearchQuery::new(params.query.clone()))
                .nearest_to(query_vec)?
                .distance_type(DistanceType::Cosine)
                .limit(params.limit as usize)
                .execute_hybrid(QueryExecutionOptions::default())
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
        results.packages.retain(|pkg| {
            // License filter
            if let Some(license_filter) = &params.license_filter {
                if !pkg.license.to_lowercase().contains(&license_filter.to_lowercase()) {
                    return false;
                }
            }
            
            // Category filter
            if let Some(category_filter) = &params.category_filter {
                if !pkg.category.to_lowercase().contains(&category_filter.to_lowercase()) {
                    return false;
                }
            }
            
            // Broken packages filter
            if !params.include_broken && pkg.broken {
                return false;
            }
            
            // Unfree packages filter  
            if !params.include_unfree && pkg.unfree {
                return false;
            }
            
            true
        });

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
        let start_time = Instant::now();
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
            .distance_type(DistanceType::Cosine)
            .limit(limit as usize)
            .execute()
            .await?;

        let batches: Vec<RecordBatch> = search_results.try_collect().await?;
        results.packages = self.arrow_batches_to_packages(batches)?;
        results.total_count = results.packages.len() as i32;
        results.query_time_ms = start_time.elapsed().as_millis() as f64;

        Ok(results)
    }

    pub async fn fts_search(&self, query: &str, limit: i32) -> Result<SearchResults, LanceDBClientError> {
        let start_time = Instant::now();
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
                results.search_type = "fallback".to_string();
            }
        }

        results.total_count = results.packages.len() as i32;
        results.query_time_ms = start_time.elapsed().as_millis() as f64;
        Ok(results)
    }

    async fn fallback_search(&self, query: &str, limit: i32) -> Result<SearchResults, LanceDBClientError> {
        let start_time = Instant::now();
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
        
        // Note: arrow_to_packages now handles scoring, including fallback scoring for missing score columns

        results.total_count = results.packages.len() as i32;
        results.query_time_ms = start_time.elapsed().as_millis() as f64;
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
        let category_col = batch.column_by_name("category")
            .and_then(|col| col.as_any().downcast_ref::<StringArray>());
        
        // Get boolean columns for package status
        use arrow::array::BooleanArray;
        let broken_col = batch.column_by_name("broken")
            .and_then(|col| col.as_any().downcast_ref::<BooleanArray>());
        let unfree_col = batch.column_by_name("unfree")
            .and_then(|col| col.as_any().downcast_ref::<BooleanArray>());
        let available_col = batch.column_by_name("available")
            .and_then(|col| col.as_any().downcast_ref::<BooleanArray>());

        // Extract LanceDB score columns (try different possible names)
        let score_col = batch.column_by_name("_distance")
            .and_then(|col| col.as_any().downcast_ref::<Float32Array>())
            .or_else(|| batch.column_by_name("_score")
                .and_then(|col| col.as_any().downcast_ref::<Float32Array>()))
            .or_else(|| batch.column_by_name("score")
                .and_then(|col| col.as_any().downcast_ref::<Float32Array>()))
            .or_else(|| batch.column_by_name("_relevance")
                .and_then(|col| col.as_any().downcast_ref::<Float32Array>()));
            
        // Also try Float64 columns
        let score_col_f64 = if score_col.is_none() {
            batch.column_by_name("_distance")
                .and_then(|col| col.as_any().downcast_ref::<Float64Array>())
                .or_else(|| batch.column_by_name("_score")
                    .and_then(|col| col.as_any().downcast_ref::<Float64Array>()))
                .or_else(|| batch.column_by_name("score")
                    .and_then(|col| col.as_any().downcast_ref::<Float64Array>()))
                .or_else(|| batch.column_by_name("_relevance")
                    .and_then(|col| col.as_any().downcast_ref::<Float64Array>()))
        } else { None };

        for i in 0..num_rows {
            // Extract relevance score from LanceDB results
            let relevance_score = if let Some(col) = score_col {
                if col.is_null(i) { 1.0 } else {
                    let distance = col.value(i) as f64;
                    // Convert distance to relevance (lower distance = higher relevance)
                    if distance > 0.0 { 1.0 / (1.0 + distance) } else { 1.0 }
                }
            } else if let Some(col) = score_col_f64 {
                if col.is_null(i) { 1.0 } else {
                    let distance = col.value(i);
                    // Convert distance to relevance (lower distance = higher relevance)
                    if distance > 0.0 { 1.0 / (1.0 + distance) } else { 1.0 }
                }
            } else {
                // If no score column found, assign decreasing relevance based on result order
                1.0 - (i as f64 * 0.001)
            };
            
            let package = Package {
                package_id: package_id_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                package_name: package_name_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                version: version_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                description: description_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                homepage: homepage_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                license: license_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                attribute_path: attribute_path_col.map(|col| col.value(i).to_string()).unwrap_or_default(),
                category: category_col.map(|col| col.value(i).to_string()).unwrap_or_else(|| "misc".to_string()),
                broken: broken_col.map(|col| !col.is_null(i) && col.value(i)).unwrap_or(false),
                unfree: unfree_col.map(|col| !col.is_null(i) && col.value(i)).unwrap_or(false),
                available: available_col.map(|col| col.is_null(i) || col.value(i)).unwrap_or(true),
                relevance_score,
            };
            packages.push(package);
        }

        Ok(packages)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rstest::*;
    use tempfile::tempdir;
    use std::env;

    #[fixture]
    fn sample_package() -> Package {
        Package {
            package_id: "nodejs-18".to_string(),
            package_name: "nodejs".to_string(),
            version: "18.17.1".to_string(),
            description: "Event-driven I/O framework for the V8 JavaScript engine".to_string(),
            homepage: "https://nodejs.org".to_string(),
            license: "MIT".to_string(),
            attribute_path: "pkgs.nodejs".to_string(),
            category: "development".to_string(),
            broken: false,
            unfree: false,
            available: true,
            relevance_score: 0.95,
        }
    }

    #[fixture]
    fn search_params() -> SearchParams {
        SearchParams {
            query: "nodejs".to_string(),
            limit: 10,
            offset: 0,
            license_filter: None,
            category_filter: None,
            include_broken: false,
            include_unfree: false,
        }
    }

    #[test]
    fn test_package_serialization() {
        let sample_package = Package {
            package_id: "nodejs-18".to_string(),
            package_name: "nodejs".to_string(),
            version: "18.17.1".to_string(),
            description: "Event-driven I/O framework for the V8 JavaScript engine".to_string(),
            homepage: "https://nodejs.org".to_string(),
            license: "MIT".to_string(),
            attribute_path: "pkgs.nodejs".to_string(),
            category: "development".to_string(),
            broken: false,
            unfree: false,
            available: true,
            relevance_score: 0.95,
        };
        let json_str = serde_json::to_string(&sample_package).unwrap();
        let deserialized: Package = serde_json::from_str(&json_str).unwrap();
        
        assert_eq!(sample_package.package_id, deserialized.package_id);
        assert_eq!(sample_package.package_name, deserialized.package_name);
        assert_eq!(sample_package.version, deserialized.version);
        assert_eq!(sample_package.description, deserialized.description);
        assert_eq!(sample_package.homepage, deserialized.homepage);
        assert_eq!(sample_package.license, deserialized.license);
        assert_eq!(sample_package.attribute_path, deserialized.attribute_path);
        assert_eq!(sample_package.relevance_score, deserialized.relevance_score);
        assert_eq!(sample_package.category, deserialized.category);
        assert_eq!(sample_package.broken, deserialized.broken);
        assert_eq!(sample_package.unfree, deserialized.unfree);
        assert_eq!(sample_package.available, deserialized.available);
    }

    #[test]
    fn test_search_params_creation() {
        let search_params = SearchParams {
            query: "nodejs".to_string(),
            limit: 10,
            offset: 0,
            license_filter: None,
            category_filter: None,
            include_broken: false,
            include_unfree: false,
        };
        assert_eq!(search_params.query, "nodejs");
        assert_eq!(search_params.limit, 10);
        assert_eq!(search_params.offset, 0);
        assert!(search_params.license_filter.is_none());
        assert!(search_params.category_filter.is_none());
        assert_eq!(search_params.include_broken, false);
        assert_eq!(search_params.include_unfree, false);
    }

    #[rstest]
    #[case("nodejs web framework", 50, 0, None, None, false, false)]
    #[case("python", 25, 10, Some("MIT".to_string()), None, false, false)]
    #[case("rust compiler", 100, 0, None, Some("development".to_string()), true, true)]
    fn test_search_params_variations(
        #[case] query: &str,
        #[case] limit: i32,
        #[case] offset: i32,
        #[case] license_filter: Option<String>,
        #[case] category_filter: Option<String>,
        #[case] include_broken: bool,
        #[case] include_unfree: bool,
    ) {
        let params = SearchParams {
            query: query.to_string(),
            limit,
            offset,
            license_filter: license_filter.clone(),
            category_filter: category_filter.clone(),
            include_broken,
            include_unfree,
        };

        assert_eq!(params.query, query);
        assert_eq!(params.limit, limit);
        assert_eq!(params.offset, offset);
        assert_eq!(params.license_filter, license_filter);
        assert_eq!(params.category_filter, category_filter);
        assert_eq!(params.include_broken, include_broken);
        assert_eq!(params.include_unfree, include_unfree);
    }

    #[test]
    fn test_lancedb_client_creation() {
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().to_str().unwrap();
        
        let client = LanceDBClient::new(db_path);
        assert!(client.is_ok());
        
        let client = client.unwrap();
        assert_eq!(client.db_path, db_path);
        assert!(client.connection.is_none());
        assert!(client.table.is_none());
    }

    #[test]
    fn test_embeddings_parsing_logic() {
        // Test the parsing logic directly instead of through env vars
        let test_cases = [
            ("1", true),
            ("true", true),
            ("TRUE", true),
            ("yes", true),
            ("YES", true),
            ("0", false),
            ("false", false),
            ("FALSE", false),
            ("no", false),
            ("random", false),
        ];
        
        for (env_value, expected) in test_cases {
            let result = env_value == "1" || env_value.to_lowercase() == "true" || env_value.to_lowercase() == "yes";
            assert_eq!(result, expected, "Failed for env_value: {}", env_value);
        }
    }

    #[test]
    fn test_lancedb_client_embeddings_default() {
        // Test default behavior when no env var is set
        let original_val = env::var("ENABLE_EMBEDDINGS").ok();
        env::remove_var("ENABLE_EMBEDDINGS");
        
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().to_str().unwrap();
        
        let client = LanceDBClient::new(db_path).unwrap();
        assert!(!client.embeddings_enabled, "Should default to false when no env var set");
        
        // Restore original value if it existed
        if let Some(val) = original_val {
            env::set_var("ENABLE_EMBEDDINGS", val);
        }
    }

    #[test]
    fn test_search_results_creation() {
        let packages = vec![
            Package {
                package_id: "test1".to_string(),
                package_name: "test-package-1".to_string(),
                version: "1.0.0".to_string(),
                description: "Test package 1".to_string(),
                homepage: "https://test1.com".to_string(),
                license: "MIT".to_string(),
                attribute_path: "pkgs.test1".to_string(),
                category: "test".to_string(),
                broken: false,
                unfree: false,
                available: true,
                relevance_score: 0.9,
            }
        ];

        let results = SearchResults {
            packages: packages.clone(),
            total_count: 1,
            query_time_ms: 15.5,
            search_type: "test".to_string(),
        };

        assert_eq!(results.packages.len(), 1);
        assert_eq!(results.total_count, 1);
        assert_eq!(results.query_time_ms, 15.5);
        assert_eq!(results.search_type, "test");
        assert_eq!(results.packages[0].package_id, "test1");
    }

    #[tokio::test]
    async fn test_health_check_without_initialization() {
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().to_str().unwrap();
        let client = LanceDBClient::new(db_path).unwrap();
        
        let health = client.health_check().await;
        assert!(!health); // Should be false since not initialized
    }

    #[test]
    fn test_lancedb_client_error_display() {
        let error = LanceDBClientError::NotInitialized;
        assert_eq!(error.to_string(), "Connection not initialized");
        
        let error = LanceDBClientError::TableNotFound("packages".to_string());
        assert_eq!(error.to_string(), "Required table not found: packages");
        
        let error = LanceDBClientError::DatabaseError("Connection failed".to_string());
        assert_eq!(error.to_string(), "Database error: Connection failed");
        
        let error = LanceDBClientError::QueryFailed("Invalid query".to_string());
        assert_eq!(error.to_string(), "Query failed: Invalid query");
    }

    #[test] 
    fn test_package_default_values() {
        let pkg = Package {
            package_id: String::new(),
            package_name: String::new(),
            version: String::new(),
            description: String::new(),
            homepage: String::new(),
            license: String::new(),
            attribute_path: String::new(),
            category: String::new(),
            broken: false,
            unfree: false,
            available: true,
            relevance_score: 0.0,
        };

        assert!(pkg.package_id.is_empty());
        assert!(pkg.package_name.is_empty());
        assert!(pkg.version.is_empty());
        assert!(pkg.description.is_empty());
        assert!(pkg.homepage.is_empty());
        assert!(pkg.license.is_empty());
        assert!(pkg.attribute_path.is_empty());
        assert!(pkg.category.is_empty());
        assert_eq!(pkg.broken, false);
        assert_eq!(pkg.unfree, false);
        assert_eq!(pkg.available, true);
        assert_eq!(pkg.relevance_score, 0.0);
    }
}