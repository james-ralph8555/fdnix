#include "bedrock_client.hpp"

#include <aws/core/client/ClientConfiguration.h>
#include <aws/core/utils/json/JsonSerializer.h>
#include <aws/core/utils/memory/stl/AWSStringStream.h>
#include <aws/bedrock-runtime/model/InvokeModelRequest.h>
#include <cstdlib>
#include <iostream>
#include <sstream>

namespace fdnix {

    using Aws::BedrockRuntime::BedrockRuntimeClient;
    using Aws::BedrockRuntime::Model::InvokeModelRequest;

    BedrockClient::BedrockClient(const std::string& region,
                                 const std::string& model_id,
                                 int output_dimensions)
        : region_(region),
          model_id_(model_id),
          output_dimensions_(output_dimensions) {

        if (region_.empty()) {
            const char* env_region = std::getenv("AWS_REGION");
            region_ = env_region ? std::string(env_region) : std::string("us-east-1");
        }

        // Allow env to override defaults
        const char* env_model = std::getenv("BEDROCK_MODEL_ID");
        if (env_model) model_id_ = env_model;
        const char* env_dims = std::getenv("BEDROCK_OUTPUT_DIMENSIONS");
        if (env_dims) output_dimensions_ = std::atoi(env_dims);

        Aws::Client::ClientConfiguration cfg;
        cfg.region = region_.c_str();
        client_ = std::make_shared<BedrockRuntimeClient>(cfg);

        std::cout << "BedrockClient created with model: " << model_id_
                  << ", dimensions: " << output_dimensions_
                  << ", region: " << region_ << std::endl;
    }

    BedrockClient::~BedrockClient() {
        std::cout << "BedrockClient destroyed" << std::endl;
    }

    std::string BedrockClient::build_request_body(const std::string& text) {
        using namespace Aws::Utils::Json;
        JsonValue root;
        root.WithString("inputText", text);
        // Titan v2 supports specifying dimensions via `dimensions`
        root.WithInteger("dimensions", output_dimensions_);
        return root.View().WriteCompact();
    }

    std::vector<double> BedrockClient::parse_embedding_response(const Aws::String& body_str) {
        using namespace Aws::Utils::Json;
        std::vector<double> embedding;
        try {
            JsonValue json(body_str);
            if (!json.WasParseSuccessful()) {
                std::cerr << "Failed to parse Bedrock response JSON" << std::endl;
                return embedding;
            }
            auto view = json.View();
            if (!view.ValueExists("embedding")) {
                std::cerr << "Bedrock response missing 'embedding'" << std::endl;
                return embedding;
            }
            auto arr = view.GetArray("embedding");
            embedding.reserve(arr.GetLength());
            for (size_t i = 0; i < arr.GetLength(); ++i) {
                embedding.push_back(arr[i].AsDouble());
            }
        } catch (const std::exception& e) {
            std::cerr << "Error parsing Bedrock embedding response: " << e.what() << std::endl;
        }
        return embedding;
    }

    std::vector<double> BedrockClient::generate_embedding(const std::string& text) {
        if (text.empty()) {
            std::cerr << "Cannot generate embedding for empty text" << std::endl;
            return {};
        }

        if (!client_) {
            std::cerr << "Bedrock client not initialized" << std::endl;
            return {};
        }

        try {
            InvokeModelRequest req;
            req.SetModelId(model_id_.c_str());
            req.SetAccept("application/json");
            req.SetContentType("application/json");

            const auto body = build_request_body(text);
            auto bodyStream = Aws::MakeShared<Aws::StringStream>("BedrockBody");
            *bodyStream << body;
            req.SetBody(bodyStream);

            auto outcome = client_->InvokeModel(req);
            if (!outcome.IsSuccess()) {
                const auto& err = outcome.GetError();
                std::cerr << "Bedrock InvokeModel error: " << err.GetExceptionName() << ": "
                          << err.GetMessage() << std::endl;
                return {};
            }

            auto& result = outcome.GetResult();
            Aws::StringStream ss;
            ss << result.GetBody().rdbuf();
            return parse_embedding_response(ss.str());

        } catch (const std::exception& e) {
            std::cerr << "Error generating embedding via Bedrock: " << e.what() << std::endl;
            return {};
        }
    }

    std::vector<std::vector<double>> BedrockClient::generate_embeddings(const std::vector<std::string>& texts) {
        std::vector<std::vector<double>> out;
        out.reserve(texts.size());
        for (const auto& t : texts) {
            auto v = generate_embedding(t);
            if (!v.empty()) out.push_back(std::move(v));
        }
        return out;
    }

    bool BedrockClient::health_check() {
        try {
            if (model_id_.empty()) return false;
            auto e = generate_embedding("test");
            return !e.empty();
        } catch (...) {
            return false;
        }
    }

} // namespace fdnix
