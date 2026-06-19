//! `worker` — the OpenAI-compatible autonomous coding-agent loop.
//!
//! Ported from lemma's `lemma-worker/src/lib.rs` and decoupled: model specs load
//! from a JSON config (`models.json`) instead of a hardcoded cohort, and the host
//! allowlist / engine-backed / progress-sink / read-only-grounding machinery is
//! stripped. What remains is the agentic loop: one OpenAI-compatible
//! chat-completions endpoint, the read/write/run tool loop, the loop guard, SSE
//! reassembly, and per-call pricing.

use std::collections::HashMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::file_tools;

/// The agent loop's iteration budget — large enough that an autonomous worker can
/// read → edit → run-tests → fix across many turns.
const MAX_AGENT_ITERS: usize = 60;
/// Loop guard: this many identical (call, result) repeats means the agent is looping —
/// solved-but-can't-stop, or stuck re-trying the same failing step. On hitting it we
/// nudge once and force the next round to be the final answer. (Observed: an open-weight
/// worker that solved the task at call 3, then re-ran the passing test ~50 more times.)
const LOOP_REPEAT_LIMIT: usize = 3;
/// Defensive cap for provider response bodies. Normal worker JSON/SSE responses
/// are far below this; if a provider or proxy returns something huge, fail before
/// reading it into memory unboundedly.
const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum WorkerError {
    #[error("http: {0}")]
    Http(String),
    #[error("api {status}: {body}")]
    Api {
        status: u16,
        body: String,
        /// `x-ratelimit-reset` (seconds to wait) when the provider sent one on a 429.
        retry_after: Option<std::time::Duration>,
    },
    #[error("config: {0}")]
    Config(String),
    /// Part of the ported error API; the driver resolves model names itself, so this
    /// is reserved for callers embedding `WorkerRegistry::get` in a fallible path.
    #[allow(dead_code)]
    #[error("no such model: {0}")]
    Unknown(String),
}

impl WorkerError {
    /// Worth a retry: network/transport blips and 429/5xx provider errors
    /// (transient) — not config/auth/unknown (permanent). Part of the ported API; the
    /// driver doesn't retry, but a caller embedding this module may.
    #[allow(dead_code)]
    pub fn is_transient(&self) -> bool {
        match self {
            WorkerError::Http(_) => true,
            WorkerError::Api { status, .. } => matches!(status, 429 | 500 | 502 | 503 | 504),
            _ => false,
        }
    }
}

/// One model definition (a `models.json` element). The field shape is the same as
/// lemma's `WorkerSpec`, so existing specs port directly; `cached_input_usd_per_mtok`
/// is added for cache-aware pricing.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerSpec {
    pub name: String,
    pub provider: String,
    /// OpenAI-compatible base URL (e.g. the Fireworks chat endpoint).
    pub base_url: String,
    pub model: String,
    /// Env var holding the bearer key (e.g. `FIREWORKS_API_KEY`).
    pub api_key_env: Option<String>,
    pub input_usd_per_mtok: f64,
    pub output_usd_per_mtok: f64,
    /// Cached-input list rate per 1M tokens. Agentic loops resend context, so most
    /// input is cache-served; this lets the driver price the cached/uncached split.
    /// Absent → falls back to the uncached input rate (cache-blind).
    #[serde(default)]
    pub cached_input_usd_per_mtok: Option<f64>,
    /// Coarse routing role hint (e.g. "coding"). Optional, informational only.
    #[serde(default)]
    pub role: Option<String>,
    /// Max output tokens per delegation (caps a runaway worker). The driver applies
    /// a default when absent.
    #[serde(default)]
    pub max_tokens: Option<u32>,
    /// Per-worker wall-clock ceiling for one HTTP delegation, in seconds. Absent →
    /// the driver default.
    #[serde(default)]
    pub timeout_secs: Option<u64>,
    /// Some upstream models reject non-streaming requests with HTTP 400
    /// `streaming_required`; set this to call them with `stream: true` and
    /// reassemble the SSE response.
    #[serde(default)]
    pub stream: bool,
    /// Provider QoS serving tier, sent as the `service_tier` request param (Fireworks:
    /// `"priority"`). Per-model because not every model supports every tier. Absent →
    /// not sent.
    #[serde(default)]
    pub service_tier: Option<String>,
    /// Reasoning-effort hint, sent as the top-level `reasoning_effort` request param.
    /// `"none"` makes a thinking-capable model emit clean, non-thinking output (the
    /// right mode for agentic coding loops where hidden reasoning can burn the
    /// tool-call budget). Per-model. Absent → not sent.
    #[serde(default)]
    pub reasoning_effort: Option<String>,
}

impl WorkerSpec {
    /// Flat (cache-blind) price for one call from its token usage.
    pub fn price(&self, prompt_tokens: i64, completion_tokens: i64) -> f64 {
        (prompt_tokens as f64 * self.input_usd_per_mtok
            + completion_tokens as f64 * self.output_usd_per_mtok)
            / 1_000_000.0
    }

    /// Cache-aware price: uncached input at `input_usd_per_mtok`, the `cached` slice at
    /// `cached_input_usd_per_mtok` (falling back to the uncached rate when absent),
    /// completion at `output_usd_per_mtok`.
    pub fn cache_aware_price(&self, prompt: i64, cached: i64, completion: i64) -> f64 {
        let cached_rate = self
            .cached_input_usd_per_mtok
            .unwrap_or(self.input_usd_per_mtok);
        let uncached = (prompt - cached).max(0);
        (uncached as f64 * self.input_usd_per_mtok
            + cached as f64 * cached_rate
            + completion as f64 * self.output_usd_per_mtok)
            / 1_000_000.0
    }
}

/// The registry of available models, loaded from `models.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerRegistry {
    pub models: Vec<WorkerSpec>,
}

impl WorkerRegistry {
    /// Load the registry from a JSON file. The file is either a top-level object
    /// `{ "models": [ ... ] }` or a bare array `[ ... ]` of specs.
    pub fn load(path: &Path) -> Result<Self, WorkerError> {
        let s = std::fs::read_to_string(path)
            .map_err(|e| WorkerError::Config(format!("reading {}: {e}", path.display())))?;
        Self::from_json(&s)
    }

    /// Parse a registry from a JSON string: either `{ "models": [...] }` or a bare
    /// `[...]` array of specs.
    pub fn from_json(s: &str) -> Result<Self, WorkerError> {
        // Try the object form first; fall back to a bare array.
        if let Ok(reg) = serde_json::from_str::<Self>(s) {
            return Ok(reg);
        }
        let models: Vec<WorkerSpec> =
            serde_json::from_str(s).map_err(|e| WorkerError::Config(e.to_string()))?;
        Ok(Self { models })
    }

    /// Index the registry by model name for lookup. Part of the ported API.
    #[allow(dead_code)]
    pub fn by_name(&self) -> HashMap<&str, &WorkerSpec> {
        self.models.iter().map(|m| (m.name.as_str(), m)).collect()
    }

    pub fn get(&self, name: &str) -> Option<&WorkerSpec> {
        self.models.iter().find(|m| m.name == name)
    }

    /// Comma-joined model names (for error/usage messages).
    pub fn names(&self) -> String {
        self.models
            .iter()
            .map(|m| m.name.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    }
}

/// The result of one agent run — the model's final text, plus the metadata the
/// driver records (model/tokens/cost/trajectory). Some fields (`worker`, `model`,
/// `cost_usd`) are part of the ported result shape; the driver reads token counts +
/// trajectory + text and recomputes cost cache-aware.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct WorkerResult {
    pub worker: String,
    pub model: String,
    pub text: String,
    pub prompt_tokens: i64,
    /// Of `prompt_tokens`, the count served from the provider's prompt cache
    /// (`usage.prompt_tokens_details.cached_tokens`). Lets a caller price the
    /// cached/uncached input split instead of flat-rating all input tokens.
    pub cached_tokens: i64,
    pub completion_tokens: i64,
    pub cost_usd: f64,
    /// One line per tool call the worker made, in order: `tool(arg) → result-head`.
    pub trajectory: Vec<String>,
}

fn agent_made_file_write(trajectory: &[String]) -> bool {
    trajectory
        .iter()
        .any(|step| step.starts_with("write_file("))
}

fn successful_verification_tool(name: &str, args: &serde_json::Value, result: &str) -> bool {
    if name != "run_command" || !result.starts_with("exit: 0") {
        return false;
    }
    let command = args
        .get("command")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let output = result.to_ascii_lowercase();

    if command.contains("unittest") {
        return output.lines().any(|line| line.trim() == "ok")
            && output.contains("ran ")
            && !output.contains("ran 0 tests");
    }
    if command.contains("pytest") || command.contains("py.test") {
        return output.contains(" passed") && !output.contains("no tests ran");
    }
    if command.contains("cargo test") {
        return output.contains("test result: ok");
    }

    [
        "go test",
        "npm test",
        "pnpm test",
        "yarn test",
        "cargo check",
        "cargo build",
        "cargo clippy",
        "python3 -m compileall",
        "python -m compileall",
        "python3 -m py_compile",
        "python -m py_compile",
        "tsc",
        "ruff",
        "mypy",
    ]
    .iter()
    .any(|needle| command.contains(needle))
}

/// Build a request body for a concrete model, including provider/model-specific
/// top-level knobs that must travel with every chat-completions request.
fn request_body_for_spec(
    spec: &WorkerSpec,
    messages: &[serde_json::Value],
    max_tokens: u32,
) -> serde_json::Value {
    let mut body = serde_json::json!({
        "model": spec.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    });
    // Some models reject non-streaming requests; ask for usage in the final
    // chunk so token accounting still works after we reassemble the SSE.
    if spec.stream {
        body["stream"] = serde_json::json!(true);
        body["stream_options"] = serde_json::json!({ "include_usage": true });
    }
    // Provider QoS serving tier (Fireworks `service_tier`), set per-model.
    if let Some(tier) = &spec.service_tier {
        body["service_tier"] = serde_json::json!(tier);
    }
    // Reasoning-effort hint (Fireworks honors it top-level), set per-model.
    if let Some(effort) = &spec.reasoning_effort {
        body["reasoning_effort"] = serde_json::json!(effort);
    }
    body
}

/// One-line summary of a tool call for the trajectory: `tool(arg) → result-head`.
/// The arg hint is whichever of path/command/pattern/query is present; the result is
/// its first line, clipped, with a `+N lines` note when there's more.
fn summarize_step(name: &str, args: &serde_json::Value, result: &str) -> String {
    let hint = args
        .get("path")
        .or_else(|| args.get("command"))
        .or_else(|| args.get("pattern"))
        .or_else(|| args.get("query"))
        .and_then(|v| v.as_str())
        .map(|s| clip(s, 60))
        .unwrap_or_default();
    let first = result.lines().next().unwrap_or("").trim();
    let extra = result.lines().count().saturating_sub(1);
    let res = if extra > 0 {
        format!("{} (+{extra} lines)", clip(first, 70))
    } else {
        clip(first, 70)
    };
    format!("{name}({hint}) → {res}")
}

/// Clip a string to `n` chars, appending `…` when truncated (char-safe).
fn clip(s: &str, n: usize) -> String {
    if s.chars().count() > n {
        let mut t: String = s.chars().take(n).collect();
        t.push('…');
        t
    } else {
        s.to_string()
    }
}

/// The autonomous agent loop: the read/write/run tool loop over an OpenAI-compatible
/// chat-completions endpoint, carrying a task end to end inside `root` (the workspace
/// it edits). Runtime-agnostic of retry/backoff/timeout — the caller owns those.
async fn run_loop(
    http: &reqwest::Client,
    spec: &WorkerSpec,
    api_key: Option<&str>,
    system: Option<&str>,
    brief: &str,
    max_tokens: u32,
    root: &Path,
) -> Result<WorkerResult, WorkerError> {
    let url = format!("{}/chat/completions", spec.base_url.trim_end_matches('/'));
    let mut messages: Vec<serde_json::Value> = Vec::new();
    if let Some(sys) = system {
        messages.push(serde_json::json!({ "role": "system", "content": sys }));
    }
    messages.push(serde_json::json!({ "role": "user", "content": brief }));

    let tools = file_tools::agent_tools();
    let max_iters = MAX_AGENT_ITERS;
    let mut prompt_tokens = 0i64;
    let mut completion_tokens = 0i64;
    // Of prompt_tokens, how many the provider served from its prompt cache. Agentic
    // loops resend the whole conversation each call, so this is usually most of the
    // input; capturing it lets a caller price the cached/uncached split.
    let mut cached_tokens = 0i64;
    let mut trajectory: Vec<String> = Vec::new();
    // Loop-guard state: a tally of identical (call, result) tuples and a latch that
    // forces a final answer once the agent is clearly looping.
    let mut call_counts: HashMap<String, usize> = HashMap::new();
    let mut force_final = false;

    for iter in 0..max_iters {
        let last = iter + 1 == max_iters || force_final;
        let mut body = request_body_for_spec(spec, &messages, max_tokens);
        // Offer tools every round except the last, where we force a final answer.
        if !last {
            body["tools"] = tools.clone();
        }
        let val = post_chat(http, &url, api_key, &body).await?;
        prompt_tokens += val["usage"]["prompt_tokens"].as_i64().unwrap_or(0);
        completion_tokens += val["usage"]["completion_tokens"].as_i64().unwrap_or(0);
        cached_tokens += val["usage"]["prompt_tokens_details"]["cached_tokens"]
            .as_i64()
            .unwrap_or(0);
        let msg = &val["choices"][0]["message"];
        let finish_reason = val["choices"][0]["finish_reason"]
            .as_str()
            .unwrap_or("unknown");

        let calls = msg["tool_calls"].as_array().filter(|c| !c.is_empty());
        if let (Some(calls), false) = (calls, last) {
            // The worker wants to act — execute each call locally, feed the result back.
            messages.push(msg.clone());
            let mut round_repeat = 0;
            let mut round_successful_verification = false;
            for call in calls {
                let id = call["id"].as_str().unwrap_or("");
                let name = call["function"]["name"].as_str().unwrap_or("");
                let fargs: serde_json::Value = call["function"]["arguments"]
                    .as_str()
                    .and_then(|s| serde_json::from_str(s).ok())
                    .unwrap_or_else(|| serde_json::json!({}));
                let result = file_tools::execute_tool(root, name, &fargs).await;
                // Tally exact (call, result) repeats — an identical call returning an
                // identical result made no progress.
                let seen = {
                    let c = call_counts
                        .entry(format!("{name}|{fargs}|{result}"))
                        .or_insert(0);
                    *c += 1;
                    *c
                };
                round_repeat = round_repeat.max(seen);
                if name == "write_file" {
                    round_successful_verification = false;
                } else if successful_verification_tool(name, &fargs, &result)
                    && agent_made_file_write(&trajectory)
                {
                    round_successful_verification = true;
                }
                trajectory.push(summarize_step(name, &fargs, &result));
                messages.push(serde_json::json!({
                    "role": "tool", "tool_call_id": id, "content": result,
                }));
            }
            if round_successful_verification {
                messages.push(serde_json::json!({
                    "role": "user",
                    "content": "Stop — verification passed after your file changes. The task is \
                        complete enough for review. Do NOT call any more tools, do \
                        not re-read files, and do not run another check. Write your short final \
                        summary now: what you changed and how you verified it.",
                }));
                force_final = true;
            }
            // A no-progress call repeated past the limit means the agent is looping:
            // nudge once and force the next round to be the final answer.
            if !force_final && round_repeat >= LOOP_REPEAT_LIMIT {
                messages.push(serde_json::json!({
                    "role": "user",
                    "content": "Stop — you have repeated the same action with the same \
                        result several times, making no progress. You are looping. The task \
                        is either already complete or you are stuck. Do NOT call any more \
                        tools. Write your final summary now: what you changed and how you \
                        verified it; or, if blocked, exactly what is blocking you.",
                }));
                force_final = true;
            }
            continue;
        }

        // No tool calls (or the forced-answer last round) → this is the result.
        let mut text = msg["content"].as_str().unwrap_or("").to_string();
        // An agent's deliverable is the file changes (the caller reads the diff), so an
        // empty closing summary is acceptable as long as it made a write.
        if text.trim().is_empty() {
            if !agent_made_file_write(&trajectory) {
                return Err(WorkerError::Api {
                    status: 0,
                    body: format!(
                        "empty agent response after {} tool calls with no file writes \
                         (finish_reason={finish_reason}, prompt_tokens={prompt_tokens}, \
                         completion_tokens={completion_tokens})",
                        trajectory.len()
                    ),
                    retry_after: None,
                });
            }
            text = "(agent finished without a summary — review the diff)".to_string();
        }
        return Ok(WorkerResult {
            worker: spec.name.clone(),
            model: spec.model.clone(),
            text,
            prompt_tokens,
            cached_tokens,
            completion_tokens,
            cost_usd: spec.price(prompt_tokens, completion_tokens),
            trajectory,
        });
    }
    Err(WorkerError::Api {
        status: 0,
        body: "worker tool loop produced no answer".into(),
        retry_after: None,
    })
}

/// Run an autonomous agent delegation: the read/write/run tool loop with a large
/// iteration budget, carrying a task end to end. Requires `root` (the workspace it
/// edits); the caller reviews the resulting diff.
pub async fn run_agent(
    http: &reqwest::Client,
    spec: &WorkerSpec,
    api_key: Option<&str>,
    system: Option<&str>,
    brief: &str,
    max_tokens: u32,
    root: &Path,
) -> Result<WorkerResult, WorkerError> {
    run_loop(http, spec, api_key, system, brief, max_tokens, root).await
}

/// Providers may send `x-ratelimit-reset` on a 429 = seconds to wait before retrying.
/// Parse it into a backoff hint; ignore anything missing, unparseable, negative, or
/// non-finite.
fn parse_retry_after(headers: &reqwest::header::HeaderMap) -> Option<std::time::Duration> {
    let secs: f64 = headers
        .get("x-ratelimit-reset")?
        .to_str()
        .ok()?
        .trim()
        .parse()
        .ok()?;
    (secs.is_finite() && secs >= 0.0).then(|| std::time::Duration::from_secs_f64(secs))
}

/// One chat-completions POST: send, status-check, parse. The retry/loop lives in
/// the callers.
async fn post_chat(
    http: &reqwest::Client,
    url: &str,
    api_key: Option<&str>,
    body: &serde_json::Value,
) -> Result<serde_json::Value, WorkerError> {
    let streaming = body
        .get("stream")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let mut req = http.post(url).json(body);
    if let Some(key) = api_key {
        req = req.bearer_auth(key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| WorkerError::Http(e.to_string()))?;
    let status = resp.status();
    // Capture the provider's retry hint before the body consumes `resp`.
    let retry_after = parse_retry_after(resp.headers());
    let text = read_response_capped(resp).await?;
    if streaming {
        if !status.is_success() {
            return Err(WorkerError::Api {
                status: status.as_u16(),
                body: text,
                retry_after,
            });
        }
        // SSE response: fold it back to the non-streaming
        // `{choices:[{message,...}],usage}` shape the caller expects.
        Ok(reassemble_stream(&text))
    } else {
        if !status.is_success() {
            return Err(WorkerError::Api {
                status: status.as_u16(),
                body: text,
                retry_after,
            });
        }
        let val: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| WorkerError::Http(e.to_string()))?;
        Ok(val)
    }
}

async fn read_response_capped(mut resp: reqwest::Response) -> Result<String, WorkerError> {
    if resp
        .content_length()
        .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
    {
        return Err(WorkerError::Http(format!(
            "response body exceeds {} byte cap",
            MAX_RESPONSE_BYTES
        )));
    }

    let mut bytes = Vec::new();
    while let Some(chunk) = resp
        .chunk()
        .await
        .map_err(|e| WorkerError::Http(e.to_string()))?
    {
        if bytes.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
            return Err(WorkerError::Http(format!(
                "response body exceeds {} byte cap",
                MAX_RESPONSE_BYTES
            )));
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

/// Fold an OpenAI-compatible SSE stream back into the non-streaming response shape
/// (`{choices:[{message:{content,tool_calls},finish_reason}], usage}`), so the rest
/// of the worker loop is identical for streaming and non-streaming models. Content
/// deltas concatenate; tool-call deltas reassemble by index (id/name set once,
/// arguments appended); usage comes from the final chunk (`include_usage`).
fn reassemble_stream(sse: &str) -> serde_json::Value {
    let mut content = String::new();
    let mut finish_reason = serde_json::Value::Null;
    let mut usage = serde_json::Value::Null;
    let mut tool_calls: Vec<serde_json::Value> = Vec::new();

    for line in sse.lines() {
        let data = match line.trim().strip_prefix("data:") {
            Some(d) => d.trim(),
            None => continue,
        };
        if data == "[DONE]" {
            break;
        }
        let chunk: serde_json::Value = match serde_json::from_str(data) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(u) = chunk.get("usage") {
            if !u.is_null() {
                usage = u.clone();
            }
        }
        let choice = &chunk["choices"][0];
        if !choice["finish_reason"].is_null() {
            finish_reason = choice["finish_reason"].clone();
        }
        let delta = &choice["delta"];
        if let Some(c) = delta["content"].as_str() {
            content.push_str(c);
        }
        if let Some(deltas) = delta["tool_calls"].as_array() {
            for tc in deltas {
                let idx = tc["index"].as_u64().unwrap_or(0) as usize;
                while tool_calls.len() <= idx {
                    tool_calls.push(serde_json::json!({
                        "id": "", "type": "function",
                        "function": { "name": "", "arguments": "" }
                    }));
                }
                let slot = &mut tool_calls[idx];
                if let Some(id) = tc["id"].as_str().filter(|s| !s.is_empty()) {
                    slot["id"] = serde_json::json!(id);
                }
                if let Some(name) = tc["function"]["name"].as_str().filter(|s| !s.is_empty()) {
                    slot["function"]["name"] = serde_json::json!(name);
                }
                if let Some(args) = tc["function"]["arguments"].as_str() {
                    let prev = slot["function"]["arguments"].as_str().unwrap_or("");
                    slot["function"]["arguments"] = serde_json::json!(format!("{prev}{args}"));
                }
            }
        }
    }

    let mut message = serde_json::json!({ "role": "assistant", "content": content });
    if !tool_calls.is_empty() {
        message["tool_calls"] = serde_json::json!(tool_calls);
    }
    serde_json::json!({
        "choices": [{ "message": message, "finish_reason": finish_reason }],
        "usage": usage,
    })
}
