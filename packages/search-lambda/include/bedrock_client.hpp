#pragma once

#include <string>
#include <vector>
#include <memory>
#include <aws/bedrock-runtime/BedrockRuntimeClient.h>

namespace fdnix {

    /**
     * @brief AWS Bedrock client for generating embeddings
     */
    class BedrockClient {
    public:
        explicit BedrockClient(const std::string& model_id = "cohere.embed-english-v3");
        ~BedrockClient();

        // Delete copy constructor and assignment operator
        BedrockClient(const BedrockClient&) = delete;
        BedrockClient& operator=(const BedrockClient&) = delete;

        /**
         * @brief Generate vector embedding for text using Bedrock
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
         * @brief Check if the Bedrock client is properly configured
         * @return true if client can make requests
         */
        bool health_check();

        /**
         * @brief Get the current model ID being used
         * @return Model ID string
         */
        const std::string& get_model_id() const { return model_id_; }

    private:
        std::string model_id_;
        std::unique_ptr<Aws::BedrockRuntime::BedrockRuntimeClient> client_;
        
        // Helper methods
        std::string prepare_request_body(const std::string& text);
        std::string prepare_request_body(const std::vector<std::string>& texts);
        std::vector<double> parse_embedding_response(const std::string& response);
        std::vector<std::vector<double>> parse_embeddings_response(const std::string& response);
    };

} // namespace fdnix