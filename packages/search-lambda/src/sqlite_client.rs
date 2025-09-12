use rusqlite::{Connection, Result, Row};
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
    db_path: String,
    connection: Option<Connection>,
}

unsafe impl Send for SQLiteClient {}
unsafe impl Sync for SQLiteClient {}

impl SQLiteClient {
    pub fn new(db_path: &str) -> Result<Self, SQLiteClientError> {
        info!("SQLiteClient created for database: {}", db_path);

        Ok(SQLiteClient {
            db_path: db_path.to_string(),
            connection: None,
        })
    }

    pub async fn initialize(&mut self) -> Result<bool, SQLiteClientError> {
        let conn = Connection::open(&self.db_path)?;

        // Check if tables exist
        {
            let mut check_stmt = conn.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='packages'")?;
            let table_exists: Option<String> = check_stmt.query_row([], |row| row.get(0)).ok();
            
            if table_exists.is_none() {
                error!("packages table not found in SQLite database at {}", self.db_path);
                return Err(SQLiteClientError::DatabaseError("packages table not found".to_string()));
            }
        }

        info!("SQLite client initialized successfully");

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
            search_type: "fts".to_string(),
        };

        let conn = self.connection.as_ref()
            .ok_or(SQLiteClientError::NotInitialized)?;

        if params.query.is_empty() {
            return Ok(results);
        }

        // Build base FTS query format for SQLite
        
        // Build WHERE clause for filters
        let mut where_clauses = Vec::new();
        let mut query_params = Vec::new();

        if let Some(license_filter) = &params.license_filter {
            where_clauses.push("license LIKE ?".to_string());
            query_params.push(format!("%{}%", license_filter));
        }

        if let Some(category_filter) = &params.category_filter {
            where_clauses.push("category LIKE ?".to_string());
            query_params.push(format!("%{}%", category_filter));
        }

        if !params.include_broken {
            where_clauses.push("broken = 0".to_string());
        }

        if !params.include_unfree {
            where_clauses.push("unfree = 0".to_string());
        }

        // Note: 'available' field removed from SearchParams structure

        let where_clause = if where_clauses.is_empty() {
            String::new()
        } else {
            format!(" AND {}", where_clauses.join(" AND "))
        };

        // Build full query - assuming packages_fts already has all the needed columns
        let base_query = if where_clause.is_empty() {
            format!("SELECT package_id, package_name, version, description, homepage, license, attribute_path, category, broken, unfree, available, bm25(packages_fts) as relevance_score FROM packages_fts WHERE packages_fts MATCH ? ORDER BY relevance_score DESC LIMIT ? OFFSET ?")
        } else {
            format!("SELECT package_id, package_name, version, description, homepage, license, attribute_path, category, broken, unfree, available, bm25(packages_fts) as relevance_score FROM packages_fts WHERE packages_fts MATCH ? {} ORDER BY relevance_score DESC LIMIT ? OFFSET ?", where_clause)
        };

        // Execute query with parameters
        let mut stmt = conn.prepare(&base_query)?;
        let mut rows = stmt.query_map(
            rusqlite::params![params.query, params.limit, params.offset],
            |row| self.row_to_package(row, true)
        )?;

        while let Some(package_result) = rows.next() {
            if let Ok(package) = package_result {
                results.packages.push(package);
            }
        }

        // Get total count for pagination
        let count_query = if where_clause.is_empty() {
            format!("SELECT COUNT(*) FROM packages_fts WHERE packages_fts MATCH ?")
        } else {
            format!("SELECT COUNT(*) FROM packages_fts CROSS JOIN packages USING(package_id) WHERE packages_fts MATCH ? {}", where_clause)
        };
        let mut count_stmt = conn.prepare(&count_query)?;
        let count: i32 = count_stmt.query_row(
            rusqlite::params![params.query],
            |row| row.get(0)
        )?;
        
        results.total_count = count;
        results.query_time_ms = start_time.elapsed().as_millis() as f64;

        Ok(results)
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

    fn row_to_package(&self, row: &Row, has_score: bool) -> Result<Package, rusqlite::Error> {
        let relevance_score = if has_score {
            row.get::<_, f64>("relevance_score")?
        } else {
            1.0 - (row.get::<_, i64>("rowid")? as f64 * 0.001)
        };

        Ok(Package {
            package_id: row.get("package_id")?,
            package_name: row.get("package_name")?,
            version: row.get("version")?,
            description: row.get("description")?,
            homepage: row.get("homepage")?,
            license: row.get("license")?,
            attribute_path: row.get("attribute_path")?,
            category: row.get("category")?,
            broken: row.get("broken")?,
            unfree: row.get("unfree")?,
            available: row.get("available")?,
            relevance_score,
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