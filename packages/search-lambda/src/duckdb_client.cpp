#include "duckdb_client.hpp"
#include <iostream>
#include <chrono>
#include <algorithm>
#include <unordered_set>
#include <unordered_map>
#include <cstdlib>
#include <sstream>

namespace fdnix {

    DuckDBClient::DuckDBClient(const std::string& db_path) 
        : db_path_(db_path), database_(nullptr), connection_(nullptr) {
        
        // Check if embeddings are enabled via environment variable
        const char* enable_embeddings = std::getenv("ENABLE_EMBEDDINGS");
        embeddings_enabled_ = enable_embeddings && 
                             (std::string(enable_embeddings) == "1" || 
                              std::string(enable_embeddings) == "true" || 
                              std::string(enable_embeddings) == "yes");
        
        std::cout << "DuckDBClient created for database: " << db_path_ 
                  << ", embeddings enabled: " << (embeddings_enabled_ ? "true" : "false") << std::endl;
    }

    DuckDBClient::~DuckDBClient() {
        // connection_ will be automatically cleaned up
        std::cout << "DuckDBClient destroyed" << std::endl;
    }

    bool DuckDBClient::initialize() {
        try {
            // Create DuckDB database instance with read-only config
            duckdb::DBConfig config;
            config.options.access_mode = duckdb::AccessMode::READ_ONLY;
            database_ = std::make_unique<duckdb::DuckDB>(db_path_, &config);
            connection_ = std::make_unique<duckdb::Connection>(*database_);
            
            // Load required extensions
            try {
                connection_->Query("LOAD fts;");
                std::cout << "FTS extension loaded successfully" << std::endl;
            } catch (const std::exception& e) {
                std::cerr << "Warning: Could not load FTS extension: " << e.what() << std::endl;
            }
            
            if (embeddings_enabled_) {
                try {
                    connection_->Query("LOAD vss;");
                    std::cout << "VSS extension loaded successfully" << std::endl;
                } catch (const std::exception& e) {
                    std::cerr << "Warning: Could not load VSS extension: " << e.what() << std::endl;
                    embeddings_enabled_ = false;  // Disable embeddings if VSS not available
                }
            }
            
            // Check if database has required tables
            auto result = connection_->Query("SELECT name FROM sqlite_master WHERE type='table' AND name='packages';");
            if (result->HasError() || result->RowCount() == 0) {
                std::cerr << "Required 'packages' table not found in database" << std::endl;
                return false;
            }
            
            // Check embeddings availability if enabled
            if (embeddings_enabled_) {
                embeddings_enabled_ = check_embeddings_availability();
                if (!embeddings_enabled_) {
                    std::cout << "Embeddings table not found or empty, falling back to FTS-only mode" << std::endl;
                }
            }
            
            std::cout << "DuckDB client initialized successfully (embeddings: " 
                      << (embeddings_enabled_ ? "enabled" : "disabled") << ")" << std::endl;
            return true;
            
        } catch (const std::exception& e) {
            std::cerr << "Error initializing DuckDB client: " << e.what() << std::endl;
            return false;
        }
    }

    SearchResults DuckDBClient::hybrid_search(const SearchParams& params, 
                                             const std::vector<double>& query_embedding) {
        auto start_time = std::chrono::high_resolution_clock::now();
        
        SearchResults results;
        
        try {
            if (embeddings_enabled_ && !query_embedding.empty()) {
                // Hybrid search mode
                results.search_type = "hybrid";
                
                // Perform vector search
                auto vector_results = vector_search(query_embedding, params.limit * 2);
                
                // Perform FTS search  
                auto fts_results = fts_search(params.query, params.limit * 2);
                
                // Combine using Reciprocal Rank Fusion
                results.packages = reciprocal_rank_fusion(
                    vector_results.packages, 
                    fts_results.packages
                );
            } else {
                // FTS-only search mode
                results.search_type = "fts";
                auto fts_results = fts_search(params.query, params.limit * 2);
                results.packages = fts_results.packages;
            }
            
            // Apply filters
            if (params.license_filter.has_value() || params.category_filter.has_value()) {
                results.packages.erase(
                    std::remove_if(results.packages.begin(), results.packages.end(),
                        [&params](const Package& pkg) {
                            if (params.license_filter.has_value() && 
                                pkg.license.find(params.license_filter.value()) == std::string::npos) {
                                return true;
                            }
                            // Category filtering would need additional metadata
                            return false;
                        }
                    ),
                    results.packages.end()
                );
            }
            
            // Apply offset and limit
            if (params.offset > 0 && params.offset < static_cast<int>(results.packages.size())) {
                results.packages.erase(results.packages.begin(), 
                                     results.packages.begin() + params.offset);
            }
            
            if (params.limit > 0 && static_cast<int>(results.packages.size()) > params.limit) {
                results.packages.resize(params.limit);
            }
            
            results.total_count = static_cast<int>(results.packages.size());
            
        } catch (const std::exception& e) {
            std::cerr << "Error in hybrid search: " << e.what() << std::endl;
            results.search_type = "error";
        }
        
        auto end_time = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time);
        results.query_time_ms = duration.count() / 1000.0;
        
        return results;
    }

    SearchResults DuckDBClient::vector_search(const std::vector<double>& query_embedding, 
                                            int limit) {
        SearchResults results;
        results.search_type = "vector";
        
        if (!connection_ || !embeddings_enabled_ || query_embedding.empty()) {
            return results;
        }
        
        try {
            // Convert vector to DuckDB FLOAT array format
            std::ostringstream vec_str;
            vec_str << "[";
            for (size_t i = 0; i < query_embedding.size(); ++i) {
                if (i > 0) vec_str << ",";
                vec_str << query_embedding[i];
            }
            vec_str << "]";
            
            // Construct VSS query using DuckDB's vss_search function
            std::ostringstream query;
            query << "SELECT p.package_id, p.packageName, p.version, p.description, "
                  << "p.homepage, p.license, p.attributePath, d.distance "
                  << "FROM vss_search('embeddings_vss_idx', " << vec_str.str() << "::FLOAT[]) AS d "
                  << "JOIN embeddings e ON e.rowid = d.rowid "
                  << "JOIN packages p ON p.package_id = e.package_id "
                  << "ORDER BY d.distance ASC "
                  << "LIMIT " << limit << ";";
            
            auto result = connection_->Query(query.str());
            
            if (!result->HasError()) {
                for (size_t row = 0; row < result->RowCount(); ++row) {
                    Package pkg;
                    pkg.packageId = result->GetValue(0, row).ToString();
                    pkg.packageName = result->GetValue(1, row).ToString();
                    pkg.version = result->GetValue(2, row).ToString();
                    pkg.description = result->GetValue(3, row).ToString();
                    pkg.homepage = result->GetValue(4, row).ToString();
                    pkg.license = result->GetValue(5, row).ToString();
                    pkg.attributePath = result->GetValue(6, row).ToString();
                    
                    // Convert distance to similarity score (lower distance = higher score)
                    double distance = result->GetValue(7, row).GetValue<double>();
                    pkg.relevanceScore = 1.0 / (1.0 + distance);
                    
                    results.packages.push_back(pkg);
                }
            } else {
                std::cerr << "Vector search query failed: " << result->GetError() << std::endl;
            }
            
        } catch (const std::exception& e) {
            std::cerr << "Error in vector search: " << e.what() << std::endl;
        }
        
        results.total_count = static_cast<int>(results.packages.size());
        return results;
    }

    SearchResults DuckDBClient::fts_search(const std::string& query, int limit) {
        SearchResults results;
        results.search_type = "fts";
        
        if (!connection_ || query.empty()) {
            return results;
        }
        
        try {
            // Escape single quotes in the query for SQL safety
            std::string escaped_query = query;
            size_t pos = 0;
            while ((pos = escaped_query.find("'", pos)) != std::string::npos) {
                escaped_query.replace(pos, 1, "''");
                pos += 2;
            }
            
            // Construct FTS query using DuckDB FTS with BM25 scoring
            std::ostringstream sql_query;
            sql_query << "SELECT p.package_id, p.packageName, p.version, p.description, "
                      << "p.homepage, p.license, p.attributePath, fts.score "
                      << "FROM (SELECT package_id, fts_main_packages_fts_source.match_bm25(package_id, '" << escaped_query << "') AS score FROM packages_fts_source) fts "
                      << "JOIN packages p ON p.package_id = fts.package_id "
                      << "WHERE fts.score IS NOT NULL "
                      << "ORDER BY fts.score DESC "
                      << "LIMIT " << limit << ";";
            
            auto result = connection_->Query(sql_query.str());
            
            if (!result->HasError()) {
                for (size_t row = 0; row < result->RowCount(); ++row) {
                    Package pkg;
                    pkg.packageId = result->GetValue(0, row).ToString();
                    pkg.packageName = result->GetValue(1, row).ToString();
                    pkg.version = result->GetValue(2, row).ToString();
                    pkg.description = result->GetValue(3, row).ToString();
                    pkg.homepage = result->GetValue(4, row).ToString();
                    pkg.license = result->GetValue(5, row).ToString();
                    pkg.attributePath = result->GetValue(6, row).ToString();
                    
                    // Use BM25 score directly as relevance score
                    pkg.relevanceScore = result->GetValue(7, row).GetValue<double>();
                    
                    results.packages.push_back(pkg);
                }
            } else {
                std::cerr << "FTS search query failed: " << result->GetError() << std::endl;
                
                // Fallback to simple LIKE search if FTS fails
                std::ostringstream fallback_query;
                fallback_query << "SELECT package_id, packageName, version, description, "
                              << "homepage, license, attributePath, 1.0 as score "
                              << "FROM packages "
                              << "WHERE packageName ILIKE '%" << escaped_query << "%' "
                              << "OR description ILIKE '%" << escaped_query << "%' "
                              << "ORDER BY CASE WHEN packageName ILIKE '%" << escaped_query << "%' THEN 1 ELSE 2 END, "
                              << "packageName "
                              << "LIMIT " << limit << ";";
                
                auto fallback_result = connection_->Query(fallback_query.str());
                if (!fallback_result->HasError()) {
                    for (size_t row = 0; row < fallback_result->RowCount(); ++row) {
                        Package pkg;
                        pkg.packageId = fallback_result->GetValue(0, row).ToString();
                        pkg.packageName = fallback_result->GetValue(1, row).ToString();
                        pkg.version = fallback_result->GetValue(2, row).ToString();
                        pkg.description = fallback_result->GetValue(3, row).ToString();
                        pkg.homepage = fallback_result->GetValue(4, row).ToString();
                        pkg.license = fallback_result->GetValue(5, row).ToString();
                        pkg.attributePath = fallback_result->GetValue(6, row).ToString();
                        pkg.relevanceScore = 1.0 - (row * 0.1);  // Simple decreasing score
                        
                        results.packages.push_back(pkg);
                    }
                }
            }
            
        } catch (const std::exception& e) {
            std::cerr << "Error in FTS search: " << e.what() << std::endl;
        }
        
        results.total_count = static_cast<int>(results.packages.size());
        return results;
    }

    bool DuckDBClient::health_check() {
        if (!connection_) {
            return false;
        }
        
        try {
            // Execute a simple query to verify database health
            auto result = connection_->Query("SELECT 1;");
            return !result->HasError();
        } catch (const std::exception& e) {
            std::cerr << "Health check failed: " << e.what() << std::endl;
            return false;
        }
    }

    std::vector<Package> DuckDBClient::reciprocal_rank_fusion(
        const std::vector<Package>& vector_results,
        const std::vector<Package>& fts_results,
        double k) {
        
        std::unordered_map<std::string, Package> combined_packages;
        std::unordered_map<std::string, double> rrf_scores;
        
        // Process vector results
        for (size_t i = 0; i < vector_results.size(); ++i) {
            const auto& pkg = vector_results[i];
            const std::string key = pkg.packageId.empty() ? pkg.packageName : pkg.packageId;
            
            // RRF score: 1 / (k + rank)
            double score = 1.0 / (k + i + 1);
            
            combined_packages[key] = pkg;
            rrf_scores[key] = score;
        }
        
        // Process FTS results and add/merge scores
        for (size_t i = 0; i < fts_results.size(); ++i) {
            const auto& pkg = fts_results[i];
            const std::string key = pkg.packageId.empty() ? pkg.packageName : pkg.packageId;
            
            // RRF score: 1 / (k + rank)
            double score = 1.0 / (k + i + 1);
            
            if (combined_packages.find(key) != combined_packages.end()) {
                // Package exists, add to RRF score
                rrf_scores[key] += score;
            } else {
                // New package from FTS
                combined_packages[key] = pkg;
                rrf_scores[key] = score;
            }
        }
        
        // Convert to vector and sort by RRF score
        std::vector<Package> result;
        for (auto& [key, pkg] : combined_packages) {
            pkg.relevanceScore = rrf_scores[key];
            result.push_back(pkg);
        }
        
        // Sort by RRF score (descending)
        std::sort(result.begin(), result.end(),
            [](const Package& a, const Package& b) {
                return a.relevanceScore > b.relevanceScore;
            });
        
        return result;
    }

    std::vector<Package> DuckDBClient::combine_and_rank_results(
        const std::vector<Package>& vector_results,
        const std::vector<Package>& fts_results,
        double /*vector_weight*/,
        double /*fts_weight*/) {
        
        // Use RRF for combining results
        return reciprocal_rank_fusion(vector_results, fts_results);
    }

    bool DuckDBClient::check_embeddings_availability() {
        if (!connection_) {
            return false;
        }
        
        try {
            // Check if embeddings table exists
            auto result = connection_->Query("SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings';");
            if (result->HasError() || result->RowCount() == 0) {
                return false;
            }
            
            // Check if embeddings table has data
            auto count_result = connection_->Query("SELECT COUNT(*) FROM embeddings WHERE vector IS NOT NULL;");
            if (count_result->HasError()) {
                return false;
            }
            
            int64_t count = count_result->GetValue(0, 0).GetValue<int64_t>();
            return count > 0;
            
        } catch (const std::exception& e) {
            std::cerr << "Error checking embeddings availability: " << e.what() << std::endl;
            return false;
        }
    }

} // namespace fdnix
