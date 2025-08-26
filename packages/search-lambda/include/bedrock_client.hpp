#pragma once

#include <string>
#include <vector>
#include <memory>
#include <aws/bedrock-runtime/BedrockRuntimeClient.h>

namespace fdnix {

    /**
     * @brief AWS Bedrock client for real-time embedding generation
     */
    class BedrockClient {
    public:
        explicit BedrockClient(const std::string& region = "",
                               const std::string& model_id = "amazon.titan-embed-text-v2:0",
                               int output_dimensions = 256);
        ~BedrockClient();

        BedrockClient(const BedrockClient&) = delete;
        BedrockClient& operator=(const BedrockClient&) = delete;

        /**
         * @brief Generate vector embedding for text using Bedrock Runtime
         */
        std::vector<double> generate_embedding(const std::string& text);

        /**
         * @brief Generate embeddings for multiple texts (sequential for now)
         */
        std::vector<std::vector<double>> generate_embeddings(const std::vector<std::string>& texts);

        /**
         * @brief Simple health check by embedding a test string
         */
        bool health_check();

        const std::string& get_model_id() const { return model_id_; }
        int get_output_dimensions() const { return output_dimensions_; }
        const std::string& get_region() const { return region_; }

    private:
        std::string region_;
        std::string model_id_;
        int output_dimensions_;
        std::shared_ptr<Aws::BedrockRuntime::BedrockRuntimeClient> client_;

        std::string build_request_body(const std::string& text);
        std::vector<double> parse_embedding_response(const Aws::String& body_str);
    };

} // namespace fdnix

