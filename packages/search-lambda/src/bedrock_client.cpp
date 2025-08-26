#include "bedrock_client.hpp"
#include <iostream>
#include <cstdlib>
#include <aws/core/client/ClientConfiguration.h>
#include <aws/core/utils/json/JsonSerializer.h>
#include <aws/bedrock-runtime/model/InvokeModelRequest.h>
#include <aws/core/utils/memory/stl/AWSStringStream.h>

namespace fdnix {

    BedrockClient::BedrockClient(const std::string& model_id, const std::string& region)
        : model_id_(model_id), region_(region), client_(nullptr) {
        // Initialize AWS Bedrock client with region from env if not provided
        std::string cfg_region = region_;
        if (cfg_region.empty()) {
            const char* env_region = std::getenv("BEDROCK_REGION");
            if (!env_region) env_region = std::getenv("AWS_REGION");
            if (env_region) cfg_region = env_region; else cfg_region = "us-east-1";
        }

        Aws::Client::ClientConfiguration config;
        config.region = cfg_region.c_str();
        client_ = std::make_unique<Aws::BedrockRuntime::BedrockRuntimeClient>(config);

        std::cout << "BedrockClient created with model: " << model_id_ 
                  << ", region: " << cfg_region << std::endl;
    }

    BedrockClient::~BedrockClient() {
        std::cout << "BedrockClient destroyed" << std::endl;
    }

    std::vector<double> BedrockClient::generate_embedding(const std::string& text) {
        if (text.empty()) {
            std::cerr << "Cannot generate embedding for empty text" << std::endl;
            return {};
        }

        try {
            if (!client_) {
                std::cerr << "Bedrock client not initialized" << std::endl;
                return {};
            }

            Aws::BedrockRuntime::Model::InvokeModelRequest request;
            request.SetModelId(model_id_.c_str());
            request.SetContentType("application/json");
            request.SetAccept("application/json");

            const auto body = prepare_request_body(text);
            auto stream = Aws::MakeShared<Aws::StringStream>("BedrockBody");
            (*stream) << body;
            request.SetBody(stream);

            auto outcome = client_->InvokeModel(request);
            if (!outcome.IsSuccess()) {
                std::cerr << "Bedrock API error: " << outcome.GetError().GetMessage() << std::endl;
                return {};
            }

            auto& result = outcome.GetResultWithOwnership();
            Aws::String payload((std::istreambuf_iterator<char>(result.GetBody())), std::istreambuf_iterator<char>());
            return parse_embedding_response(std::string(payload.c_str()));

        } catch (const std::exception& e) {
            std::cerr << "Error generating embedding: " << e.what() << std::endl;
            return {};
        }
    }

    std::vector<std::vector<double>> BedrockClient::generate_embeddings(const std::vector<std::string>& texts) {
        std::vector<std::vector<double>> embeddings;
        embeddings.reserve(texts.size());

        // TODO: Implement batch processing for efficiency
        // For now, process each text individually
        for (const auto& text : texts) {
            auto embedding = generate_embedding(text);
            if (!embedding.empty()) {
                embeddings.push_back(std::move(embedding));
            }
        }

        return embeddings;
    }

    bool BedrockClient::health_check() {
        try {
            // Simple test embedding
            if (model_id_.empty() || !client_) return false;
            auto test_embedding = generate_embedding("test");
            return !test_embedding.empty();

        } catch (const std::exception& e) {
            std::cerr << "Bedrock health check failed: " << e.what() << std::endl;
            return false;
        }
    }

    std::string BedrockClient::prepare_request_body(const std::string& text) {
        // Cohere v3 on Bedrock: {"texts":[text],"input_type":"search_document","truncate":"END","embedding_types":["float"]}
        using namespace Aws::Utils::Json;
        JsonValue request_json;
        Aws::Utils::Array<JsonValue> texts_array(1);
        texts_array[0] = JsonValue(text);
        request_json.WithArray("texts", texts_array);
        request_json.WithString("input_type", "search_document");
        request_json.WithString("truncate", "END");
        Aws::Utils::Array<JsonValue> types_arr(1);
        types_arr[0] = JsonValue("float");
        request_json.WithArray("embedding_types", types_arr);
        return request_json.View().WriteCompact();
    }

    std::string BedrockClient::prepare_request_body(const std::vector<std::string>& texts) {
        using namespace Aws::Utils::Json;
        JsonValue request_json;
        Aws::Utils::Array<JsonValue> texts_array(texts.size());
        for (size_t i = 0; i < texts.size(); ++i) {
            texts_array[i] = JsonValue(texts[i]);
        }
        request_json.WithArray("texts", texts_array);
        request_json.WithString("input_type", "search_document");
        request_json.WithString("truncate", "END");
        Aws::Utils::Array<JsonValue> types_arr(1);
        types_arr[0] = JsonValue("float");
        request_json.WithArray("embedding_types", types_arr);
        return request_json.View().WriteCompact();
    }

    std::vector<double> BedrockClient::parse_embedding_response(const std::string& response) {
        // Parse Cohere v3 embeddings response; handle variants
        try {
            using namespace Aws::Utils::Json;
            
            JsonValue response_json(response);
            if (!response_json.WasParseSuccessful()) {
                std::cerr << "Failed to parse Bedrock response JSON" << std::endl;
                return {};
            }

            std::vector<double> embedding;

            if (!response_json.ValueExists("embeddings")) {
                return embedding;
            }

            auto embeddings_array = response_json.GetArray("embeddings");
            if (embeddings_array.GetLength() == 0) return embedding;

            const auto first = embeddings_array[0];
            if (first.IsListType()) {
                // Direct float array
                auto values = first.GetArray();
                embedding.reserve(values.GetLength());
                for (size_t i = 0; i < values.GetLength(); ++i) {
                    embedding.push_back(values[i].AsDouble());
                }
                return embedding;
            }

            if (first.IsObject()) {
                auto obj = first.AsObject();
                if (obj.ValueExists("float")) {
                    auto values = obj.GetArray("float");
                    embedding.reserve(values.GetLength());
                    for (size_t i = 0; i < values.GetLength(); ++i) {
                        embedding.push_back(values[i].AsDouble());
                    }
                    return embedding;
                }
                if (obj.ValueExists("embedding")) {
                    auto values = obj.GetArray("embedding");
                    embedding.reserve(values.GetLength());
                    for (size_t i = 0; i < values.GetLength(); ++i) {
                        embedding.push_back(values[i].AsDouble());
                    }
                    return embedding;
                }
            }

            return embedding;

        } catch (const std::exception& e) {
            std::cerr << "Error parsing embedding response: " << e.what() << std::endl;
            return {};
        }
    }

    std::vector<std::vector<double>> BedrockClient::parse_embeddings_response(const std::string& response) {
        // Batch response parsing; handle variants
        try {
            using namespace Aws::Utils::Json;
            
            JsonValue response_json(response);
            if (!response_json.WasParseSuccessful()) {
                std::cerr << "Failed to parse Bedrock batch response JSON" << std::endl;
                return {};
            }

            std::vector<std::vector<double>> embeddings;
            
            if (response_json.ValueExists("embeddings")) {
                auto embeddings_array = response_json.GetArray("embeddings");
                embeddings.reserve(embeddings_array.GetLength());
                
                for (size_t i = 0; i < embeddings_array.GetLength(); ++i) {
                    const auto elem = embeddings_array[i];
                    std::vector<double> embedding;
                    if (elem.IsListType()) {
                        auto embedding_values = elem.GetArray();
                        embedding.reserve(embedding_values.GetLength());
                        for (size_t j = 0; j < embedding_values.GetLength(); ++j) {
                            embedding.push_back(embedding_values[j].AsDouble());
                        }
                    } else if (elem.IsObject()) {
                        auto obj = elem.AsObject();
                        if (obj.ValueExists("float")) {
                            auto embedding_values = obj.GetArray("float");
                            embedding.reserve(embedding_values.GetLength());
                            for (size_t j = 0; j < embedding_values.GetLength(); ++j) {
                                embedding.push_back(embedding_values[j].AsDouble());
                            }
                        } else if (obj.ValueExists("embedding")) {
                            auto embedding_values = obj.GetArray("embedding");
                            embedding.reserve(embedding_values.GetLength());
                            for (size_t j = 0; j < embedding_values.GetLength(); ++j) {
                                embedding.push_back(embedding_values[j].AsDouble());
                            }
                        }
                    }
                    embeddings.push_back(std::move(embedding));
                }
            }

            return embeddings;

        } catch (const std::exception& e) {
            std::cerr << "Error parsing batch embedding response: " << e.what() << std::endl;
            return {};
        }
    }

} // namespace fdnix
