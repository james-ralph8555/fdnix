#pragma once

#include <string>
#include <vector>
#include <memory>
#include <optional>
#include <duckdb.hpp>

namespace fdnix {

    /**
     * @brief Package metadata structure
     */
    struct Package {
        std::string packageId;       // Prefer attributePath or name@version
        std::string packageName;
        std::string version;
        std::string description;
        std::string homepage;
        std::string license;
        std::string attributePath;   // Matches DB schema
        double relevanceScore = 0.0; // CamelCase for API response
    };

    /**
     * @brief Search parameters
     */
    struct SearchParams {
        std::string query;
        int limit = 50;
        int offset = 0;
        std::optional<std::string> license_filter;
        std::optional<std::string> category_filter;
    };

    /**
     * @brief Search results container
     */
    struct SearchResults {
        std::vector<Package> packages;
        int total_count = 0;
        double query_time_ms = 0.0;
        std::string search_type; // "vector", "fts", "hybrid"
    };

    /**
     * @brief DuckDB client for hybrid search operations
     */
    class DuckDBClient {
    public:
        explicit DuckDBClient(const std::string& db_path);
        ~DuckDBClient();

        // Delete copy constructor and assignment operator
        DuckDBClient(const DuckDBClient&) = delete;
        DuckDBClient& operator=(const DuckDBClient&) = delete;

        /**
         * @brief Initialize the database connection and load extensions
         * @return true if successful, false otherwise
         */
        bool initialize();

        /**
         * @brief Perform hybrid search combining vector and FTS results
         * @param params Search parameters
         * @param query_embedding Vector embedding for the search query
         * @return Search results
         */
        SearchResults hybrid_search(const SearchParams& params, 
                                   const std::vector<double>& query_embedding);

        /**
         * @brief Perform vector similarity search only
         * @param query_embedding Vector embedding for the search query
         * @param limit Number of results to return
         * @return Search results
         */
        SearchResults vector_search(const std::vector<double>& query_embedding, 
                                  int limit = 50);

        /**
         * @brief Perform full-text search only
         * @param query Search query string
         * @param limit Number of results to return
         * @return Search results
         */
        SearchResults fts_search(const std::string& query, int limit = 50);

        /**
         * @brief Check if the database connection is healthy
         * @return true if connection is active and working
         */
        bool health_check();

    private:
        std::string db_path_;
        std::unique_ptr<duckdb::DuckDB> database_;
        std::unique_ptr<duckdb::Connection> connection_;
        bool embeddings_enabled_;
        
        // Helper methods
        std::vector<Package> combine_and_rank_results(
            const std::vector<Package>& vector_results,
            const std::vector<Package>& fts_results,
            double vector_weight = 0.6,
            double fts_weight = 0.4
        );
        
        // RRF (Reciprocal Rank Fusion) implementation
        std::vector<Package> reciprocal_rank_fusion(
            const std::vector<Package>& vector_results,
            const std::vector<Package>& fts_results,
            double k = 60.0
        );
        
        bool check_embeddings_availability();
    };

} // namespace fdnix
