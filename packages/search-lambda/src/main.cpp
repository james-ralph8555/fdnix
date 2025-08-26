#include <iostream>
#include <string>
#include <cstdlib>
#include <memory>
#include <aws/lambda-runtime/runtime.h>
#include <aws/core/Aws.h>
#include <aws/core/utils/json/JsonSerializer.h>
#include <aws/core/utils/memory/stl/AWSString.h>
#include "duckdb_client.hpp"
#include "gemini_client.hpp"

using namespace aws::lambda_runtime;

// Global clients (initialized once)
static std::unique_ptr<fdnix::DuckDBClient> g_duckdb_client;
static std::unique_ptr<fdnix::GeminiClient> g_gemini_client;

invocation_response handler(invocation_request const& request)
{
    using namespace Aws::Utils::Json;
    
    try {
        // Parse the Lambda event (API Gateway request)
        JsonValue event(request.payload);
        
        // Extract query parameters
        std::string query_param;
        int limit = 50;
        int offset = 0;
        
        if (event.View().ValueExists("queryStringParameters") && 
            !event.View().GetObject("queryStringParameters").IsNull()) {
            auto query_params = event.View().GetObject("queryStringParameters");
            
            if (query_params.ValueExists("q")) {
                query_param = query_params.GetString("q");
            }
            if (query_params.ValueExists("limit")) {
                limit = std::stoi(query_params.GetString("limit"));
            }
            if (query_params.ValueExists("offset")) {
                offset = std::stoi(query_params.GetString("offset"));
            }
        }
        
        // Handle search request
        if (!query_param.empty() && g_duckdb_client && g_gemini_client) {
            // Generate embedding for the query
            auto query_embedding = g_gemini_client->generate_embedding(query_param);
            
            if (!query_embedding.empty()) {
                // Perform hybrid search
                fdnix::SearchParams search_params;
                search_params.query = query_param;
                search_params.limit = limit;
                search_params.offset = offset;
                // Optional filters
                if (event.View().ValueExists("queryStringParameters") && !event.View().GetObject("queryStringParameters").IsNull()) {
                    auto qps = event.View().GetObject("queryStringParameters");
                    if (qps.ValueExists("license")) {
                        search_params.license_filter = qps.GetString("license");
                    }
                    if (qps.ValueExists("category")) {
                        search_params.category_filter = qps.GetString("category");
                    }
                }
                
                auto results = g_duckdb_client->hybrid_search(search_params, query_embedding);
                
                // Create response JSON
                JsonValue response_body;
                response_body.WithString("message", "Search completed");
                response_body.WithString("query", query_param);
                response_body.WithInteger("total_count", results.total_count);
                response_body.WithDouble("query_time_ms", results.query_time_ms);
                response_body.WithString("search_type", results.search_type);
                
                // Add packages array
                Aws::Utils::Array<JsonValue> packages_array(results.packages.size());
                for (size_t i = 0; i < results.packages.size(); ++i) {
                    JsonValue pkg;
                    pkg.WithString("packageId", results.packages[i].packageId);
                    pkg.WithString("packageName", results.packages[i].packageName);
                    pkg.WithString("version", results.packages[i].version);
                    pkg.WithString("description", results.packages[i].description);
                    pkg.WithString("homepage", results.packages[i].homepage);
                    pkg.WithString("license", results.packages[i].license);
                    pkg.WithString("attributePath", results.packages[i].attributePath);
                    pkg.WithDouble("relevanceScore", results.packages[i].relevanceScore);
                    packages_array[i] = pkg;
                }
                response_body.WithArray("packages", packages_array);
                
                // Create API Gateway response
                JsonValue api_response;
                api_response.WithInteger("statusCode", 200);
                api_response.WithString("body", response_body.View().WriteCompact());
                
                JsonValue headers;
                headers.WithString("Content-Type", "application/json");
                headers.WithString("Access-Control-Allow-Origin", "*");
                api_response.WithObject("headers", headers);
                
                return invocation_response::success(api_response.View().WriteCompact(), "application/json");
            }
        }
        
        // Default stub response
        JsonValue response_body;
        response_body.WithString("message", "fdnix search API (C++) — stub active");
        response_body.WithString("note", "This is a C++ Lambda stub. DuckDB integration ready.");
        response_body.WithString("version", "0.1.0");
        response_body.WithString("runtime", "provided.al2023");
        
        if (!query_param.empty()) {
            response_body.WithString("query_received", query_param);
        }
        
        // Environment variables check
        const char* duckdb_path = std::getenv("DUCKDB_PATH");
        const char* duckdb_lib_path = std::getenv("DUCKDB_LIB_PATH");
        const char* gemini_key = std::getenv("GOOGLE_GEMINI_API_KEY");
        const char* gemini_model = std::getenv("GEMINI_MODEL_ID");
        
        if (duckdb_path) {
            response_body.WithString("duckdb_path", duckdb_path);
        }
        if (duckdb_lib_path) {
            response_body.WithString("duckdb_lib_path", duckdb_lib_path);
        }
        if (gemini_key && gemini_model) {
            response_body.WithString("gemini_model_id", gemini_model);
        }
        
        // Add client status
        response_body.WithBool("duckdb_initialized", g_duckdb_client != nullptr);
        response_body.WithBool("gemini_initialized", g_gemini_client != nullptr);
        
        if (g_duckdb_client) {
            response_body.WithBool("duckdb_healthy", g_duckdb_client->health_check());
        }
        if (g_gemini_client) {
            response_body.WithBool("gemini_healthy", g_gemini_client->health_check());
        }
        
        // Create API Gateway response
        JsonValue api_response;
        api_response.WithInteger("statusCode", 200);
        api_response.WithString("body", response_body.View().WriteCompact());
        
        JsonValue headers;
        headers.WithString("Content-Type", "application/json");
        headers.WithString("Access-Control-Allow-Origin", "*");
        api_response.WithObject("headers", headers);
        
        return invocation_response::success(api_response.View().WriteCompact(), "application/json");
        
    } catch (const std::exception& e) {
        // Error handling
        JsonValue error_response;
        error_response.WithInteger("statusCode", 500);
        
        JsonValue error_body;
        error_body.WithString("error", "Internal server error");
        error_body.WithString("message", e.what());
        error_response.WithString("body", error_body.View().WriteCompact());
        
        JsonValue headers;
        headers.WithString("Content-Type", "application/json");
        headers.WithString("Access-Control-Allow-Origin", "*");
        error_response.WithObject("headers", headers);
        
        return invocation_response::success(error_response.View().WriteCompact(), "application/json");
    }
}

int main()
{
    // Initialize AWS SDK
    Aws::SDKOptions options;
    Aws::InitAPI(options);
    
    std::cout << "Starting fdnix-search-api C++ Lambda" << std::endl;
    
    // Initialize global clients
    try {
        const char* duckdb_path = std::getenv("DUCKDB_PATH");
        const char* gemini_key = std::getenv("GOOGLE_GEMINI_API_KEY");
        const char* gemini_model = std::getenv("GEMINI_MODEL_ID");
        
        if (duckdb_path) {
            std::cout << "Initializing DuckDB client with path: " << duckdb_path << std::endl;
            g_duckdb_client = std::make_unique<fdnix::DuckDBClient>(duckdb_path);
            if (!g_duckdb_client->initialize()) {
                std::cerr << "Failed to initialize DuckDB client" << std::endl;
                g_duckdb_client.reset();
            }
        } else {
            std::cerr << "DUCKDB_PATH environment variable not set" << std::endl;
        }
        
        if (gemini_key) {
            std::cout << "Initializing Gemini client with model: " << (gemini_model ? gemini_model : "default") << std::endl;
            g_gemini_client = std::make_unique<fdnix::GeminiClient>(gemini_key, gemini_model ? gemini_model : "gemini-embedding-001");
        } else {
            std::cerr << "GOOGLE_GEMINI_API_KEY environment variable not set" << std::endl;
        }
        
    } catch (const std::exception& e) {
        std::cerr << "Error initializing clients: " << e.what() << std::endl;
    }
    
    std::cout << "Lambda initialization complete. Starting runtime..." << std::endl;
    
    // Run the Lambda runtime
    run_handler(handler);
    
    // Cleanup clients
    g_duckdb_client.reset();
    g_gemini_client.reset();
    
    // Cleanup AWS SDK
    Aws::ShutdownAPI(options);
    
    return 0;
}
