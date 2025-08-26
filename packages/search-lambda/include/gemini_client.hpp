#pragma once

#include <string>
#include <vector>
#include <memory>
#include <aws/core/http/HttpClient.h>
#include <aws/core/http/HttpClientFactory.h>

namespace fdnix {

    /**
     * @brief Google Gemini API client for generating embeddings
     */
    class GeminiClient {
    public:
        explicit GeminiClient(const std::string& api_key = "",
                             const std::string& model_id = "gemini-embedding-001",
                             int output_dimensions = 256);
        ~GeminiClient();

        // Delete copy constructor and assignment operator
        GeminiClient(const GeminiClient&) = delete;
        GeminiClient& operator=(const GeminiClient&) = delete;

        /**
         * @brief Generate vector embedding for text using Gemini API
         * @param text Input text to embed
         * @return Vector embedding as doubles, empty if failed
         */
        std::vector<double> generate_embedding(const std::string& text);

        /**
         * @brief Generate vector embeddings for multiple texts
         * @param texts Vector of input texts to embed
         * @return Vector of embeddings, empty if failed
         */
        std::vector<std::vector<double>> generate_embeddings(const std::vector<std::string>& texts);

        /**
         * @brief Check if the Gemini client is properly configured
         * @return true if client can make requests
         */
        bool health_check();

        /**
         * @brief Get the current model ID being used
         * @return Model ID string
         */
        const std::string& get_model_id() const { return model_id_; }

        /**
         * @brief Get the configured output dimensions
         * @return Output dimensions
         */
        int get_output_dimensions() const { return output_dimensions_; }

    private:
        std::string api_key_;
        std::string model_id_;
        std::string base_url_;
        int output_dimensions_;
        std::string task_type_;
        std::shared_ptr<Aws::Http::HttpClient> http_client_;
        
        // Helper methods
        std::string prepare_request_body(const std::string& text);
        std::string prepare_request_body(const std::vector<std::string>& texts);
        std::vector<double> parse_embedding_response(const std::string& response);
        std::vector<std::vector<double>> parse_embeddings_response(const std::string& response);
        
        // HTTP utilities
        std::shared_ptr<Aws::Http::HttpRequest> create_request(const std::string& body);
        std::string make_request(const std::string& body);
    };

} // namespace fdnix