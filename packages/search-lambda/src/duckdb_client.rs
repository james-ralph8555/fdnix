use duckdb::{Connection, Result as DuckDBResult};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
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
pub enum DuckDBClientError {
    #[error("Database error: {0}")]
    DatabaseError(#[from] duckdb::Error),
    #[error("Connection not initialized")]
    NotInitialized,
    #[error("Required table not found: {0}")]
    TableNotFound(String),
    #[error("Query failed: {0}")]
    QueryFailed(String),
}

pub struct DuckDBClient {
    db_path: String,
    connection: Option<Connection>,
    embeddings_enabled: bool,
}

impl DuckDBClient {
    pub fn new(db_path: &str) -> Result<Self, DuckDBClientError> {
        let embeddings_enabled = env::var("ENABLE_EMBEDDINGS")
            .map(|val| val == "1" || val.to_lowercase() == "true" || val.to_lowercase() == "yes")
            .unwrap_or(false);

        info!(
            "DuckDBClient created for database: {}, embeddings enabled: {}",
            db_path, embeddings_enabled
        );

        Ok(DuckDBClient {
            db_path: db_path.to_string(),
            connection: None,
            embeddings_enabled,
        })
    }

    pub fn initialize(&mut self) -> Result<bool, DuckDBClientError> {
        // Open database connection with read-only access
        let conn = Connection::open_with_flags(
            &self.db_path,
            duckdb::Config::default()
                .access_mode(duckdb::AccessMode::ReadOnly)?
        )?;

        // Set home directory to prevent extension loading issues
        if let Err(e) = conn.execute("SET home_directory = '/tmp';", []) {
            warn!("Could not set home directory: {}", e);
        }

        info!("Using DuckDB with built-in extensions: FTS, JSON");

        // Check if database has required tables
        let mut stmt = conn.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='packages';")?;
        let rows: Result<Vec<String>, _> = stmt
            .query_map([], |row| Ok(row.get::<_, String>(0)?))?
            .collect();

        match rows {
            Ok(names) if names.is_empty() => {
                error!("Required 'packages' table not found in database");
                return Err(DuckDBClientError::TableNotFound("packages".to_string()));
            }
            Ok(_) => {
                info!("Found required 'packages' table");
            }
            Err(e) => {
                error!("Error checking for packages table: {}", e);
                return Err(DuckDBClientError::DatabaseError(e));
            }
        }

        // Check embeddings availability if enabled
        if self.embeddings_enabled {
            if self.check_embeddings_availability(&conn) {
                info!("Embeddings available and enabled");
            } else {
                warn!("VSS extension not available in this build - disabling embeddings, using FTS-only mode");
                self.embeddings_enabled = false;
            }
        }

        self.connection = Some(conn);

        info!(
            "DuckDB client initialized successfully (embeddings: {})",
            if self.embeddings_enabled { "enabled" } else { "disabled" }
        );

        Ok(true)
    }

    pub fn hybrid_search(
        &self,
        params: &SearchParams,
        query_embedding: Option<&[f64]>,
    ) -> Result<SearchResults, DuckDBClientError> {
        let start_time = Instant::now();
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "unknown".to_string(),
        };

        let _conn = self.connection.as_ref()
            .ok_or(DuckDBClientError::NotInitialized)?;

        if self.embeddings_enabled && query_embedding.is_some() && !query_embedding.unwrap().is_empty() {
            // Hybrid search mode
            results.search_type = "hybrid".to_string();

            // Perform vector search
            let vector_results = self.vector_search(query_embedding.unwrap(), params.limit * 2)?;

            // Perform FTS search
            let fts_results = self.fts_search(&params.query, params.limit * 2)?;

            // Combine using Reciprocal Rank Fusion
            results.packages = self.reciprocal_rank_fusion(&vector_results.packages, &fts_results.packages, 60.0);
        } else {
            // FTS-only search mode
            results.search_type = "fts".to_string();
            let fts_results = self.fts_search(&params.query, params.limit * 2)?;
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
                // Category filtering would need additional metadata
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

    pub fn vector_search(&self, query_embedding: &[f64], limit: i32) -> Result<SearchResults, DuckDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "vector".to_string(),
        };

        let conn = self.connection.as_ref()
            .ok_or(DuckDBClientError::NotInitialized)?;

        if !self.embeddings_enabled || query_embedding.is_empty() {
            return Ok(results);
        }

        // Convert vector to DuckDB FLOAT array format
        let vec_str = format!(
            "[{}]",
            query_embedding
                .iter()
                .map(|v| v.to_string())
                .collect::<Vec<_>>()
                .join(",")
        );

        // Construct VSS query using DuckDB's vss_search function
        let query = format!(
            r#"
            SELECT p.package_id, p.packageName, p.version, p.description,
                   p.homepage, p.license, p.attributePath, d.distance
            FROM vss_search('embeddings_vss_idx', {}::FLOAT[]) AS d
            JOIN embeddings e ON e.rowid = d.rowid
            JOIN packages p ON p.package_id = e.package_id
            ORDER BY d.distance ASC
            LIMIT {};
            "#,
            vec_str, limit
        );

        let mut stmt = conn.prepare(&query)?;
        let rows = stmt.query_map([], |row| {
            let distance: f64 = row.get(7)?;
            Ok(Package {
                package_id: row.get(0)?,
                package_name: row.get(1)?,
                version: row.get(2)?,
                description: row.get(3)?,
                homepage: row.get(4)?,
                license: row.get(5)?,
                attribute_path: row.get(6)?,
                // Convert distance to similarity score (lower distance = higher score)
                relevance_score: 1.0 / (1.0 + distance),
            })
        })?;

        results.packages = rows.collect::<Result<Vec<_>, _>>()?;
        results.total_count = results.packages.len() as i32;

        Ok(results)
    }

    pub fn fts_search(&self, query: &str, limit: i32) -> Result<SearchResults, DuckDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "fts".to_string(),
        };

        let conn = self.connection.as_ref()
            .ok_or(DuckDBClientError::NotInitialized)?;

        if query.is_empty() {
            return Ok(results);
        }

        // Escape single quotes in the query for SQL safety
        let escaped_query = query.replace("'", "''");

        // Construct FTS query using DuckDB FTS with BM25 scoring
        let fts_query = format!(
            r#"
            SELECT p.package_id, p.packageName, p.version, p.description,
                   p.homepage, p.license, p.attributePath, fts.score
            FROM (SELECT package_id, fts_main_packages_fts_source.match_bm25(package_id, '{}') AS score 
                  FROM packages_fts_source) fts
            JOIN packages p ON p.package_id = fts.package_id
            WHERE fts.score IS NOT NULL
            ORDER BY fts.score DESC
            LIMIT {};
            "#,
            escaped_query, limit
        );

        let mut stmt = match conn.prepare(&fts_query) {
            Ok(stmt) => stmt,
            Err(e) => {
                error!("FTS search query failed: {}, falling back to simple search", e);
                return self.fallback_search(&escaped_query, limit);
            }
        };

        let rows = stmt.query_map([], |row| {
            Ok(Package {
                package_id: row.get(0)?,
                package_name: row.get(1)?,
                version: row.get(2)?,
                description: row.get(3)?,
                homepage: row.get(4)?,
                license: row.get(5)?,
                attribute_path: row.get(6)?,
                relevance_score: row.get(7)?, // BM25 score
            })
        })?;

        results.packages = rows.collect::<Result<Vec<_>, _>>()?;
        results.total_count = results.packages.len() as i32;

        Ok(results)
    }

    fn fallback_search(&self, escaped_query: &str, limit: i32) -> Result<SearchResults, DuckDBClientError> {
        let mut results = SearchResults {
            packages: Vec::new(),
            total_count: 0,
            query_time_ms: 0.0,
            search_type: "fts".to_string(),
        };

        let conn = self.connection.as_ref()
            .ok_or(DuckDBClientError::NotInitialized)?;

        // Fallback to simple LIKE search if FTS fails
        let fallback_query = format!(
            r#"
            SELECT package_id, packageName, version, description,
                   homepage, license, attributePath, 1.0 as score
            FROM packages
            WHERE packageName ILIKE '%{}%'
               OR description ILIKE '%{}%'
            ORDER BY CASE WHEN packageName ILIKE '%{}%' THEN 1 ELSE 2 END,
                     packageName
            LIMIT {};
            "#,
            escaped_query, escaped_query, escaped_query, limit
        );

        let mut stmt = conn.prepare(&fallback_query)?;
        let rows = stmt.query_map([], |row| {
            Ok(Package {
                package_id: row.get(0)?,
                package_name: row.get(1)?,
                version: row.get(2)?,
                description: row.get(3)?,
                homepage: row.get(4)?,
                license: row.get(5)?,
                attribute_path: row.get(6)?,
                relevance_score: row.get(7)?,
            })
        })?;

        let mut packages: Vec<Package> = rows.collect::<Result<Vec<_>, _>>()?;
        
        // Assign decreasing scores for fallback results
        for (i, pkg) in packages.iter_mut().enumerate() {
            pkg.relevance_score = 1.0 - (i as f64 * 0.1);
        }

        results.packages = packages;
        results.total_count = results.packages.len() as i32;

        Ok(results)
    }

    pub fn health_check(&self) -> bool {
        if let Some(conn) = &self.connection {
            match conn.execute("SELECT 1;", []) {
                Ok(_) => true,
                Err(e) => {
                    error!("Health check failed: {}", e);
                    false
                }
            }
        } else {
            false
        }
    }

    fn reciprocal_rank_fusion(
        &self,
        vector_results: &[Package],
        fts_results: &[Package],
        k: f64,
    ) -> Vec<Package> {
        let mut combined_packages: HashMap<String, Package> = HashMap::new();
        let mut rrf_scores: HashMap<String, f64> = HashMap::new();

        // Process vector results
        for (i, pkg) in vector_results.iter().enumerate() {
            let key = if pkg.package_id.is_empty() {
                &pkg.package_name
            } else {
                &pkg.package_id
            };

            // RRF score: 1 / (k + rank)
            let score = 1.0 / (k + i as f64 + 1.0);

            combined_packages.insert(key.clone(), pkg.clone());
            rrf_scores.insert(key.clone(), score);
        }

        // Process FTS results and add/merge scores
        for (i, pkg) in fts_results.iter().enumerate() {
            let key = if pkg.package_id.is_empty() {
                &pkg.package_name
            } else {
                &pkg.package_id
            };

            // RRF score: 1 / (k + rank)
            let score = 1.0 / (k + i as f64 + 1.0);

            if let Some(existing_score) = rrf_scores.get_mut(key) {
                // Package exists, add to RRF score
                *existing_score += score;
            } else {
                // New package from FTS
                combined_packages.insert(key.clone(), pkg.clone());
                rrf_scores.insert(key.clone(), score);
            }
        }

        // Convert to vector and sort by RRF score
        let mut result: Vec<Package> = combined_packages
            .into_iter()
            .map(|(key, mut pkg)| {
                pkg.relevance_score = rrf_scores[&key];
                pkg
            })
            .collect();

        // Sort by RRF score (descending)
        result.sort_by(|a, b| b.relevance_score.partial_cmp(&a.relevance_score).unwrap());

        result
    }

    fn check_embeddings_availability(&self, conn: &Connection) -> bool {
        // Check if embeddings table exists
        let table_check = conn.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings';");
        if let Ok(mut stmt) = table_check {
            let rows: Result<Vec<String>, _> = stmt
                .query_map([], |row| Ok(row.get::<_, String>(0)?))
                .unwrap()
                .collect();

            match rows {
                Ok(names) if names.is_empty() => return false,
                Err(_) => return false,
                _ => {}
            }
        } else {
            return false;
        }

        // Check if embeddings table has data
        let count_check = conn.prepare("SELECT COUNT(*) FROM embeddings WHERE vector IS NOT NULL;");
        if let Ok(mut stmt) = count_check {
            if let Ok(mut rows) = stmt.query([]) {
                if let Ok(Some(row)) = rows.next() {
                    if let Ok(count) = row.get::<_, i64>(0) {
                        return count > 0;
                    }
                }
            }
        }

        false
    }
}