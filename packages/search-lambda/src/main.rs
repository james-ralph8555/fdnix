use lambda_http::{run, service_fn, Body, Error, Request, Response};
use lambda_http::http::StatusCode;
use serde_json::json;
use tracing::{info, Level};
use tracing_subscriber::EnvFilter;

async fn handler(_event: Request) -> Result<Response<Body>, Error> {
    let body = json!({
        "message": "fdnix search API (Rust) — stub active",
        "note": "This is a Rust Lambda stub. Implement hybrid search next.",
    });
    let resp = Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "application/json")
        .header("Access-Control-Allow-Origin", "*")
        .body(Body::from(body.to_string()))?;
    Ok(resp)
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    let filter_layer = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter_layer)
        .with_target(false)
        .with_max_level(Level::INFO)
        .init();

    info!("Starting fdnix-search-api Rust Lambda stub");
    run(service_fn(handler)).await
}

