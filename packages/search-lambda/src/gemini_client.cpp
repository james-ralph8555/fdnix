#include "gemini_client.hpp"
#include <iostream>
#include <cstdlib>
#include <aws/core/http/HttpRequest.h>
#include <aws/core/http/HttpResponse.h>
#include <aws/core/utils/json/JsonSerializer.h>
#include <aws/core/utils/StringUtils.h>
#include <aws/core/utils/memory/stl/AWSStringStream.h>
#include <aws/core/client/ClientConfiguration.h>

namespace fdnix {

    GeminiClient::GeminiClient(const std::string& api_key, const std::string& model_id, int output_dimensions)
        : api_key_(api_key), model_id_(model_id), output_dimensions_(output_dimensions), task_type_("SEMANTIC_SIMILARITY") {
        
        // Get API key from env if not provided
        if (api_key_.empty()) {
            const char* env_key = std::getenv("GOOGLE_GEMINI_API_KEY");
            if (env_key) api_key_ = env_key;
        }
        
        // Get model from env if default
        if (model_id_ == "gemini-embedding-001") {
            const char* env_model = std::getenv("GEMINI_MODEL_ID");
            if (env_model) model_id_ = env_model;
        }
        
        // Get dimensions from env if default
        if (output_dimensions_ == 256) {
            const char* env_dims = std::getenv("GEMINI_OUTPUT_DIMENSIONS");
            if (env_dims) output_dimensions_ = std::atoi(env_dims);
        }
        
        // Get task type from env
        const char* env_task = std::getenv("GEMINI_TASK_TYPE");
        if (env_task) task_type_ = env_task;

        base_url_ = "https://generativelanguage.googleapis.com/v1beta/models/" + model_id_ + ":embedContent";
        
        // Initialize HTTP client
        http_client_ = Aws::Http::CreateHttpClient(Aws::Client::ClientConfiguration());

        std::cout << "GeminiClient created with model: " << model_id_ 
                  << ", dimensions: " << output_dimensions_
                  << ", task_type: " << task_type_ << std::endl;
    }

    GeminiClient::~GeminiClient() {
        std::cout << "GeminiClient destroyed" << std::endl;
    }

    std::vector<double> GeminiClient::generate_embedding(const std::string& text) {
        if (text.empty()) {
            std::cerr << "Cannot generate embedding for empty text" << std::endl;
            return {};
        }

        try {
            if (api_key_.empty()) {
                std::cerr << "Gemini API key not configured" << std::endl;
                return {};
            }

            const auto body = prepare_request_body(text);
            const auto response = make_request(body);
            return parse_embedding_response(response);

        } catch (const std::exception& e) {
            std::cerr << "Error generating embedding: " << e.what() << std::endl;
            return {};
        }
    }

    std::vector<std::vector<double>> GeminiClient::generate_embeddings(const std::vector<std::string>& texts) {
        std::vector<std::vector<double>> embeddings;
        embeddings.reserve(texts.size());

        // For now, process each text individually
        // TODO: Implement batch processing when supported by API
        for (const auto& text : texts) {
            auto embedding = generate_embedding(text);
            if (!embedding.empty()) {
                embeddings.push_back(std::move(embedding));
            }
        }

        return embeddings;
    }

    bool GeminiClient::health_check() {
        try {
            if (api_key_.empty() || model_id_.empty()) return false;
            auto test_embedding = generate_embedding("test");
            return !test_embedding.empty();

        } catch (const std::exception& e) {
            std::cerr << "Gemini health check failed: " << e.what() << std::endl;
            return false;
        }
    }

    std::string GeminiClient::prepare_request_body(const std::string& text) {
        using namespace Aws::Utils::Json;
        
        // Create the request structure for Gemini API
        JsonValue request_json;
        request_json.WithString("model", "models/" + model_id_);
        
        // Content structure
        JsonValue content;
        Aws::Utils::Array<JsonValue> parts(1);
        JsonValue part;
        part.WithString("text", text);
        parts[0] = part;
        content.WithArray("parts", parts);
        request_json.WithObject("content", content);
        
        // Task type and output dimensionality
        request_json.WithString("taskType", task_type_);
        request_json.WithInteger("outputDimensionality", output_dimensions_);
        
        return request_json.View().WriteCompact();
    }

    std::string GeminiClient::prepare_request_body(const std::vector<std::string>& texts) {
        // For batch processing - currently just use first text
        // TODO: Implement proper batch support when API supports it
        if (texts.empty()) return "";
        return prepare_request_body(texts[0]);
    }

    std::shared_ptr<Aws::Http::HttpRequest> GeminiClient::create_request(const std::string& body) {
        auto request = Aws::Http::CreateHttpRequest(
            Aws::Http::URI(base_url_),
            Aws::Http::HttpMethod::HTTP_POST,
            Aws::Utils::Stream::DefaultResponseStreamFactoryMethod
        );
        
        // Set headers
        request->SetHeaderValue("x-goog-api-key", api_key_);
        request->SetHeaderValue("Content-Type", "application/json");
        request->SetHeaderValue("User-Agent", "fdnix-search-lambda/1.0");
        
        // Set body
        auto bodyStream = Aws::MakeShared<Aws::StringStream>("GeminiBody");
        *bodyStream << body;
        request->AddContentBody(bodyStream);
        
        return request;
    }

    std::string GeminiClient::make_request(const std::string& body) {
        if (!http_client_) {
            throw std::runtime_error("HTTP client not initialized");
        }
        
        auto request = create_request(body);
        auto response = http_client_->MakeRequest(request);
        
        if (!response) {
            throw std::runtime_error("Failed to make HTTP request");
        }
        
        // Check response code
        auto response_code = response->GetResponseCode();
        if (response_code != Aws::Http::HttpResponseCode::OK) {
            std::string error_msg = "HTTP error " + std::to_string(static_cast<int>(response_code));
            if (response->GetResponseBody()) {
                std::ostringstream body_stream;
                body_stream << response->GetResponseBody().rdbuf();
                error_msg += ": " + body_stream.str();
            }
            throw std::runtime_error(error_msg);
        }
        
        // Read response body
        if (!response->GetResponseBody()) {
            throw std::runtime_error("Empty response body");
        }
        
        std::ostringstream response_stream;
        response_stream << response->GetResponseBody().rdbuf();
        return response_stream.str();
    }

    std::vector<double> GeminiClient::parse_embedding_response(const std::string& response) {
        try {
            using namespace Aws::Utils::Json;
            
            JsonValue response_json(response);
            if (!response_json.WasParseSuccessful()) {
                std::cerr << "Failed to parse Gemini response JSON" << std::endl;
                return {};
            }

            std::vector<double> embedding;

            if (!response_json.View().ValueExists("embedding")) {
                std::cerr << "No embedding found in response" << std::endl;
                return embedding;
            }

            auto embedding_obj = response_json.View().GetObject("embedding");
            if (!embedding_obj.ValueExists("values")) {
                std::cerr << "No values found in embedding object" << std::endl;
                return embedding;
            }

            auto values_array = embedding_obj.GetArray("values");
            embedding.reserve(values_array.GetLength());
            
            for (size_t i = 0; i < values_array.GetLength(); ++i) {
                embedding.push_back(values_array[i].AsDouble());
            }

            return embedding;

        } catch (const std::exception& e) {
            std::cerr << "Error parsing embedding response: " << e.what() << std::endl;
            return {};
        }
    }

    std::vector<std::vector<double>> GeminiClient::parse_embeddings_response(const std::string& response) {
        // For now, parse single embedding and wrap in vector
        // TODO: Implement proper batch response parsing when API supports it
        std::vector<std::vector<double>> embeddings;
        auto embedding = parse_embedding_response(response);
        if (!embedding.empty()) {
            embeddings.push_back(std::move(embedding));
        }
        return embeddings;
    }

} // namespace fdnix