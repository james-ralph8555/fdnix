#pragma once

#include <string>
#include <vector>
#include <memory>
#include <optional>

namespace fdnix {

    // Forward declaration for DuckDB connection (to be implemented)
    class DuckDBConnection;

    /**
     * @brief Package metadata structure
     */
    struct Package {
        std::string name;
        std::string version;
        std::string description;
        std::string homepage;
        std::string license;
        std::string attribute_path;
        double relevance_score = 0.0;
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
        std::unique_ptr<DuckDBConnection> connection_;
        
        // Helper methods (to be implemented)
        std::vector<Package> combine_and_rank_results(
            const std::vector<Package>& vector_results,
            const std::vector<Package>& fts_results,
            double vector_weight = 0.6,
            double fts_weight = 0.4
        );
    };

} // namespace fdnix