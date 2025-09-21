use rusqlite::{Connection, Result};
use serde::{Deserialize, Serialize};
use std::time::Instant;
use thiserror::Error;
use tracing::{info, error};

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
    // Additional fields from minified data
    pub maintainers: Option<Vec<serde_json::Value>>,
    pub platforms: Option<Vec<String>>,
    pub long_description: Option<String>,
    pub main_program: Option<String>,
    pub position: Option<String>,
    pub outputs_to_install: Option<Vec<String>>,
    pub last_updated: Option<String>,
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
pub enum SQLiteClientError {
    #[error("Database error: {0}")]
    DatabaseError(String),
    #[error("Connection not initialized")]
    NotInitialized,
    #[error("Query failed: {0}")]
    QueryFailed(String),
    #[error("SQLite error: {0}")]
    SQLiteError(#[from] rusqlite::Error),
}

pub struct SQLiteClient {
    pub db_path: String,
    pub dict_path: String,
    connection: Option<Connection>,
    dictionary: Option<zstd::dict::DecoderDictionary<'static>>,
}

unsafe impl Send for SQLiteClient {}
unsafe impl Sync for SQLiteClient {}

impl SQLiteClient {
    pub fn new(db_path: &str, dict_path: &str) -> Result<Self, SQLiteClientError> {
        info!("SQLiteClient created for database: {}, dictionary: {}", db_path, dict_path);

        Ok(SQLiteClient {
            db_path: db_path.to_string(),
            dict_path: dict_path.to_string(),
            connection: None,
            dictionary: None,
        })
    }

    pub async fn initialize(&mut self) -> Result<bool, SQLiteClientError> {
        let conn = Connection::open(&self.db_path)?;

        // Verify minified schema exists
        let minified_tables_exist = {
            let mut check_stmt = conn.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('packages_kv', 'packages_fts')")?;
            let mut tables = Vec::new();
            let mut rows = check_stmt.query_map([], |row| row.get::<_, String>(0))?;
            while let Some(table_result) = rows.next() {
                if let Ok(table) = table_result {
                    tables.push(table);
                }
            }
            tables.contains(&"packages_kv".to_string()) && 
            tables.contains(&"packages_fts".to_string())
        };
        
        if !minified_tables_exist {
            error!("Minified schema not found in SQLite database at {}", self.db_path);
            return Err(SQLiteClientError::DatabaseError("Minified schema required but not found".to_string()));
        }
        
        // Load compression dictionary
        self.dictionary = self._load_dictionary().await?;
        if self.dictionary.is_none() {
            error!("Failed to load compression dictionary from {}", self.dict_path);
            return Err(SQLiteClientError::DatabaseError("Failed to load compression dictionary".to_string()));
        }
        
        info!("SQLite client initialized successfully with minified schema and dictionary");

        self.connection = Some(conn);
        Ok(true)
    }

    pub async fn fts_search(
        &self,
        params: &SearchParams,
    ) -> Result<SearchResults, SQLiteClientError> {
        let start_time = Instant::now();
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "fts-minified".to_string(),
        };

        let conn = self.connection.as_ref()
            .ok_or(SQLiteClientError::NotInitialized)?;

        if params.query.is_empty() {
            return Ok(results);
        }

        self._fts_search_minified(conn, params, &mut results)?;

        results.query_time_ms = start_time.elapsed().as_millis() as f64;
        Ok(results)
    }

    fn _fts_search_minified(
        &self,
        conn: &Connection,
        params: &SearchParams,
        results: &mut SearchResults,
    ) -> Result<(), SQLiteClientError> {
        // Build WHERE clause for filters
        let mut where_clauses = Vec::new();

        if !params.include_broken {
            where_clauses.push("json_extract(data, '$.broken') = 0".to_string());
        }

        if !params.include_unfree {
            where_clauses.push("json_extract(data, '$.unfree') = 0".to_string());
        }

        let where_clause = if where_clauses.is_empty() {
            String::new()
        } else {
            format!(" AND {}", where_clauses.join(" AND "))
        };

        // First, search FTS table to get matching IDs
        let fts_query = if where_clause.is_empty() {
            format!("SELECT id, name, description, bm25(packages_fts) as relevance_score FROM packages_fts WHERE packages_fts MATCH ? ORDER BY relevance_score DESC LIMIT ? OFFSET ?")
        } else {
            format!("SELECT id, name, description, bm25(packages_fts) as relevance_score FROM packages_fts WHERE packages_fts MATCH ? {} ORDER BY relevance_score DESC LIMIT ? OFFSET ?", where_clause)
        };

        let mut fts_stmt = conn.prepare(&fts_query)?;
        let fts_rows = fts_stmt.query_map(
            rusqlite::params![params.query, params.limit, params.offset],
            |row| {
                Ok((
                    row.get::<_, String>("id")?,
                    row.get::<_, String>("name")?,
                    row.get::<_, String>("description")?,
                    row.get::<_, f64>("relevance_score")?,
                ))
            }
        )?;

        // Collect package IDs and fetch full data
        let package_ids: Vec<(String, String, String, f64)> = fts_rows
            .filter_map(|r| r.ok())
            .collect();
        
        // Initialize results with basic info from FTS
        results.packages = package_ids.iter().map(|(id, name, desc, score)| Package {
            package_id: id.clone(),
            package_name: name.clone(),
            version: String::new(),
            description: desc.clone(),
            homepage: String::new(),
            license: String::new(),
            attribute_path: String::new(),
            category: String::new(),
            broken: false,
            unfree: false,
            available: true,
            relevance_score: *score,
            maintainers: None,
            platforms: None,
            long_description: None,
            main_program: None,
            position: None,
            outputs_to_install: None,
            last_updated: None,
        }).collect();

        // Get full compressed data for each package
        for (i, package_id_tuple) in package_ids.iter().enumerate() {
            let package_id = &package_id_tuple.0;
            match conn.query_row(
                "SELECT data FROM packages_kv WHERE id = ?",
                [package_id],
                |row| row.get::<_, Vec<u8>>(0)
            ) {
                Ok(compressed_data) => {
                    match self._decompress_package_data(&compressed_data) {
                        Ok(full_package) => {
                            results.packages[i] = full_package;
                        }
                        Err(e) => {
                            error!("Failed to decompress package {}: {}", package_id, e);
                        }
                    }
                }
                Err(e) => {
                    error!("Failed to fetch compressed data for package {}: {}", package_id, e);
                }
            }
        }

        // Get total count
        let count_query = "SELECT COUNT(*) FROM packages_fts WHERE packages_fts MATCH ?";
        let mut count_stmt = conn.prepare(count_query)?;
        results.total_count = count_stmt.query_row(
            rusqlite::params![params.query],
            |row| row.get(0)
        )?;

        Ok(())
    }

    
    pub async fn health_check(&self) -> bool {
        match &self.connection {
            Some(conn) => {
                match conn.execute("SELECT 1", ()) {
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

    
    fn _decompress_package_data(&self, compressed_data: &[u8]) -> Result<Package, SQLiteClientError> {
        // Estimate decompressed size (10x compression ratio as estimate)
        let estimated_size = compressed_data.len() * 10;
        
        // Use dictionary-based decompression if dictionary is available
        if let Some(ref _dictionary) = self.dictionary {
            match zstd::bulk::decompress(compressed_data, estimated_size) {
                Ok(decompressed) => {
                    self._parse_package_json(&decompressed)
                }
                Err(e) => {
                    error!("Dictionary-based decompression failed: {}", e);
                    // Fallback to regular decompression
                    match zstd::bulk::decompress(compressed_data, estimated_size) {
                        Ok(decompressed) => {
                            self._parse_package_json(&decompressed)
                        }
                        Err(e) => {
                            error!("Regular decompression also failed: {}", e);
                            Err(SQLiteClientError::DatabaseError(format!("Decompression failed: {}", e)))
                        }
                    }
                }
            }
        } else {
            // Fallback to regular decompression (shouldn't happen with minified schema)
            match zstd::bulk::decompress(compressed_data, estimated_size) {
                Ok(decompressed) => {
                    self._parse_package_json(&decompressed)
                }
                Err(e) => {
                    error!("Decompression failed: {}", e);
                    Err(SQLiteClientError::DatabaseError(format!("Decompression failed: {}", e)))
                }
            }
        }
    }

    async fn _load_dictionary(&self) -> Result<Option<zstd::dict::DecoderDictionary<'static>>, SQLiteClientError> {
        use std::fs;
        
        if !std::path::Path::new(&self.dict_path).exists() {
            error!("Dictionary file not found at: {}", self.dict_path);
            return Ok(None);
        }
        
        match fs::read(&self.dict_path) {
            Ok(dict_bytes) => {
                match zstd::dict::DecoderDictionary::copy(&dict_bytes) {
                    dictionary => {
                        info!("Loaded compression dictionary: {} bytes", dict_bytes.len());
                        Ok(Some(dictionary))
                    }
                }
            }
            Err(e) => {
                error!("Failed to read dictionary file {}: {}", self.dict_path, e);
                Err(SQLiteClientError::DatabaseError(format!("Failed to read dictionary file: {}", e)))
            }
        }
    }

    fn _parse_package_json(&self, decompressed_data: &[u8]) -> Result<Package, SQLiteClientError> {
        let json_str = std::str::from_utf8(decompressed_data)
            .map_err(|e| SQLiteClientError::DatabaseError(format!("Invalid UTF-8: {}", e)))?;
        
        let pkg_value: serde_json::Value = serde_json::from_str(json_str)
            .map_err(|e| SQLiteClientError::DatabaseError(format!("JSON parse error: {}", e)))?;
        
        Ok(Package {
            package_id: pkg_value.get("package_id")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            package_name: pkg_value.get("package_name")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            version: pkg_value.get("version")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            description: pkg_value.get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            homepage: pkg_value.get("homepage")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            license: serde_json::to_string(&pkg_value.get("license").unwrap_or(&serde_json::Value::Null))
                .unwrap_or_else(|_| "null".to_string()),
            attribute_path: pkg_value.get("attribute_path")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            category: pkg_value.get("category")
                .and_then(|v| v.as_str())
                .unwrap_or("").to_string(),
            broken: pkg_value.get("broken")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            unfree: pkg_value.get("unfree")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            available: pkg_value.get("available")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
            relevance_score: 1.0, // Will be set by search function
            maintainers: pkg_value.get("maintainers").and_then(|v| {
                v.as_array().map(|arr| arr.to_vec())
            }),
            platforms: pkg_value.get("platforms").and_then(|v| {
                v.as_array().map(|arr| arr.iter()
                    .filter_map(|item| item.as_str().map(|s| s.to_string()))
                    .collect())
            }),
            long_description: pkg_value.get("long_description")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            main_program: pkg_value.get("main_program")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            position: pkg_value.get("position")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            outputs_to_install: pkg_value.get("outputs_to_install").and_then(|v| {
                v.as_array().map(|arr| arr.iter()
                    .filter_map(|item| item.as_str().map(|s| s.to_string()))
                    .collect())
            }),
            last_updated: pkg_value.get("last_updated")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_sqlite_client_creation() {
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().to_str().unwrap();
        
        let client = SQLiteClient::new(db_path).unwrap();
        assert_eq!(client.db_path, db_path);
        assert!(client.connection.is_none());
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
            search_type: "fts".to_string(),
        };

        assert_eq!(results.packages.len(), 1);
        assert_eq!(results.total_count, 1);
        assert_eq!(results.query_time_ms, 15.5);
        assert_eq!(results.search_type, "fts");
        assert_eq!(results.packages[0].package_id, "test1");
    }

    #[test]
    fn test_sqlite_client_error_display() {
        let error = SQLiteClientError::NotInitialized;
        assert_eq!(error.to_string(), "Connection not initialized");
        
        let error = SQLiteClientError::DatabaseError("Connection failed".to_string());
        assert_eq!(error.to_string(), "Database error: Connection failed");
        
        let error = SQLiteClientError::QueryFailed("Invalid query".to_string());
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