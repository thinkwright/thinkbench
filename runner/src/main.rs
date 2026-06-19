//! thinkbench runner — drive any OpenAI-compatible model through the autonomous
//! coding-agent loop against the thinkbench task suite, grade each run, and persist
//! results. Each (task, model, trial) runs the real agent loop (`worker::run_agent`)
//! in an isolated workspace; we capture solved / fractional score / latency / tokens
//! (incl. CACHED) / cache-aware cost / tool-calls, persist the full per-run record
//! (the model's actual code + trajectory + scorecard), and print a comparison table.
//! The hidden grader (`grade.py`) is copied into the workspace only AFTER the model
//! stops, so the agent never sees the test.
//!
//! Ported from lemma's `lemma-worker/examples/agent_bench.rs`; decoupled from lemma
//! (model specs load from `models.json`).
//!
//! ## Usage
//!
//!   runner <model-name> [<model-name> ...]
//!   runner --list                # list discovered tasks + configured models, no API calls
//!
//! Model names resolve against `models.json`. Each named model needs its
//! `api_key_env` (e.g. `FIREWORKS_API_KEY`) set in the environment.
//!
//! ## Config via env
//!
//!   THINKBENCH_MODELS=<path>   model config JSON (default: <crate>/models.json)
//!   THINKBENCH_TASKS=<dir>     task suite dir   (default: <crate>/../tasks)
//!   THINKBENCH_TRIALS=<n>      trials per (task, model) (default: 3)
//!   THINKBENCH_EFFORT=none|native   thinking mode (default: none = no-think parity)
//!   THINKBENCH_RESULTS=<dir>   results root (default: <crate>/../results/runs)
//!   THINKBENCH_PARALLEL=<n>    max concurrent runs (default: 4)
//!
//! Each task dir holds: a `brief.txt` (the prompt), an optional `setup/` (starter +
//! visible test) copied into the fresh workspace — absent for greenfield "implement"
//! tasks, which start empty — and a hidden `grade.py` that prints a JSON scorecard
//! (`{score, passed, total, import_ok, ...}`) and exits 0. `reference/` (if present)
//! is never touched here.

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::task::JoinSet;

mod file_tools;
mod process_env;
mod worker;

use worker::{run_agent, WorkerError, WorkerRegistry, WorkerSpec};

/// The autonomous coding-agent system prompt.
const AGENT_SYSTEM: &str = "You are an autonomous coding agent working inside a project \
directory. You have tools to read (read_file, grep, list_dir), write (write_file), and run \
shell commands (run_command). Carry the task to completion and then stop.\n\n\
How to work:\n\
- Stay on the task. Make only the changes it requires — no refactoring, reformatting, or \
touching unrelated files. When unsure of scope, make the smallest change that satisfies the \
task.\n\
- Loop: read what you need, change with write_file, then VERIFY with run_command (build / run \
the relevant tests). If a command fails, read the error and fix it — never repeat the same \
failing step.\n\
- Act, don't narrate. Each turn is a tool call that makes progress or verifies — not \
commentary or thinking aloud. Don't re-read files you've already read.\n\
- Don't fabricate. Check names, paths, and APIs against the real files before using them.\n\
- Your turns are limited. Spend them on progress, not exploration.\n\n\
You are DONE when the task is satisfied AND you have verified it (tests/build pass, or you've \
confirmed the change works). Then stop calling tools and write a SHORT summary: which files you \
changed and how you verified. If you are blocked, say what is blocking and what you tried — do \
not loop.";

/// Default model config: `<crate>/models.json`.
const DEFAULT_MODELS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/models.json");
/// Default task suite: `<crate>/../tasks`.
const DEFAULT_TASKS_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../tasks");
/// Default results root (one timestamped dir per invocation lands under here).
const DEFAULT_RESULTS_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../results/runs");
const DEFAULT_TRIALS: usize = 3;
const DEFAULT_PARALLEL: usize = 4;
/// Generous output ceiling so a large single file-write isn't truncated (which on a
/// strict provider can corrupt the tool call).
const MAX_TOKENS: u32 = 32768;
/// Wall-clock ceiling per (task, model, trial) run, so a flailing model can't hang
/// the bench.
const RUN_TIMEOUT: Duration = Duration::from_secs(600);
const GRADE_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_AGENT_ATTEMPTS: usize = 3;

struct Outcome {
    task: String,
    worker: String,
    trial: usize,
    solved: bool,
    score: f64,
    passed: i64,
    total: i64,
    import_ok: bool,
    observed: bool,
    secs: f64,
    prompt_tokens: i64,
    cached_tokens: i64,
    completion_tokens: i64,
    cost_usd: f64,
    calls: usize,
    note: String,
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let to = dst.join(entry.file_name());
        let from = entry.path();
        let meta = std::fs::symlink_metadata(&from)?;
        let file_type = meta.file_type();
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            copy_dir(&entry.path(), &to)?;
        } else if file_type.is_file() {
            std::fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

fn seed_workspace(ws: &Path, setup: &Path, slug: &str) -> std::io::Result<()> {
    let _ = std::fs::remove_dir_all(ws);
    std::fs::create_dir_all(ws)?;
    if setup.is_dir() {
        let dest = if setup.join(slug).is_dir() {
            ws.to_path_buf()
        } else {
            ws.join(slug)
        };
        copy_dir(setup, &dest)?;
    }
    Ok(())
}

enum GraderRun {
    Completed(Output),
    TimedOut(Output),
}

fn run_grader(ws: &Path) -> std::io::Result<GraderRun> {
    run_grader_with_timeout(ws, GRADE_TIMEOUT)
}

fn run_grader_with_timeout(ws: &Path, timeout: Duration) -> std::io::Result<GraderRun> {
    let mut cmd = Command::new("python3");
    cmd.arg("grade.py")
        .current_dir(ws)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    process_env::apply_std_command_env(&mut cmd, ws);

    let mut child = cmd.spawn()?;
    let start = Instant::now();
    loop {
        if child.try_wait()?.is_some() {
            return child.wait_with_output().map(GraderRun::Completed);
        }
        if start.elapsed() >= timeout {
            let _ = child.kill();
            return child.wait_with_output().map(GraderRun::TimedOut);
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

fn retry_delay(error: &WorkerError, attempt: usize) -> Duration {
    match error {
        WorkerError::Api {
            retry_after: Some(wait),
            ..
        } => (*wait).min(Duration::from_secs(30)),
        _ => Duration::from_millis(500 * 2_u64.saturating_pow(attempt.saturating_sub(1) as u32)),
    }
}

fn models_path() -> PathBuf {
    std::env::var("THINKBENCH_MODELS")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_MODELS))
}

fn tasks_dir() -> PathBuf {
    std::env::var("THINKBENCH_TASKS")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_TASKS_DIR))
}

fn results_root() -> PathBuf {
    std::env::var("THINKBENCH_RESULTS")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_RESULTS_ROOT))
}

fn discover_tasks(dir: &Path) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = match std::fs::read_dir(dir) {
        Ok(rd) => rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.is_dir() && p.join("brief.txt").is_file())
            .collect(),
        Err(e) => {
            eprintln!("error reading tasks dir {}: {e}", dir.display());
            Vec::new()
        }
    };
    dirs.sort();
    dirs
}

/// Append one JSONL row to results.jsonl (live, so a mid-run crash keeps prior rows).
fn append_jsonl(path: &Path, row: &serde_json::Value) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(f, "{row}");
    }
}

/// One unit of work: a (task, model, trial) run, with everything the worker thread
/// needs to be self-contained (paths + the resolved spec + the prompt).
struct Job {
    slug: String,
    brief: String,
    setup: PathBuf,
    task_dir: PathBuf,
    worker_name: String,
    spec: WorkerSpec,
    model_id: String,
    api_key: Option<String>,
    max_tokens: u32,
    trial: usize,
}

#[tokio::main]
async fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let list_only = args.iter().any(|a| a == "--list");
    args.retain(|a| !a.starts_with("--"));

    let models_file = models_path();
    let reg = match WorkerRegistry::load(&models_file) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("failed to load models from {}: {e}", models_file.display());
            std::process::exit(1);
        }
    };

    let tdir = tasks_dir();
    let tasks = discover_tasks(&tdir);

    // --list (or a dry path) needs no API key: report what would run and exit.
    if list_only {
        println!("models config: {}", models_file.display());
        println!("configured models: {}", reg.names());
        println!("tasks dir: {}", tdir.display());
        println!("discovered tasks: {}", tasks.len());
        for t in &tasks {
            println!("  {}", t.file_name().unwrap().to_string_lossy());
        }
        return;
    }

    let workers: Vec<String> = if args.is_empty() {
        eprintln!(
            "usage: runner <model-name> [<model-name> ...]   (configured: {})\n       \
             runner --list   to list tasks + models without running",
            reg.names()
        );
        std::process::exit(2);
    } else {
        args
    };

    let trials: usize = std::env::var("THINKBENCH_TRIALS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_TRIALS);
    let parallel: usize = std::env::var("THINKBENCH_PARALLEL")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(DEFAULT_PARALLEL);
    // Parity: models nothink by default. "native" leaves each model's configured
    // effort untouched (for a thinking-on A/B).
    let effort_mode = std::env::var("THINKBENCH_EFFORT").unwrap_or_else(|_| "none".to_string());

    if tasks.is_empty() {
        eprintln!("no tasks under {}", tdir.display());
        std::process::exit(1);
    }

    let http = reqwest::Client::new();
    // PID-scoped so parallel runner processes can't clobber each other's workspaces.
    let bench_root = std::env::temp_dir().join(format!("thinkbench-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&bench_root);

    let run_id = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let results_dir = results_root().join(run_id.to_string());
    if let Err(e) = std::fs::create_dir_all(&results_dir) {
        eprintln!(
            "failed to create results dir {}: {e}",
            results_dir.display()
        );
        std::process::exit(1);
    }
    let jsonl = Arc::new(results_dir.join("results.jsonl"));

    // Validate the named models up front, resolve their specs + keys.
    let mut resolved: Vec<(String, WorkerSpec, Option<String>)> = Vec::new();
    for worker in &workers {
        let Some(base_spec) = reg.get(worker.as_str()) else {
            eprintln!("unknown model '{worker}' (configured: {})", reg.names());
            std::process::exit(2);
        };
        let mut spec = base_spec.clone();
        if effort_mode != "native" {
            spec.reasoning_effort = Some(effort_mode.clone());
        }
        let api_key = spec
            .api_key_env
            .as_ref()
            .and_then(|v| std::env::var(v).ok());
        if api_key.is_none() {
            if let Some(env) = &spec.api_key_env {
                eprintln!("warning: {env} not set for model '{worker}' — its runs will fail");
            }
        }
        resolved.push((worker.clone(), spec, api_key));
    }

    let total_runs = tasks.len() * workers.len() * trials;
    eprintln!(
        "thinkbench runner (run {run_id}): {} task(s) x {} model(s) x {} trial(s) = {} runs\n  \
         effort={effort_mode}  parallel={parallel}  tasks={}\n  results → {}\n",
        tasks.len(),
        workers.len(),
        trials,
        total_runs,
        tdir.display(),
        results_dir.display(),
    );

    // Record the run config up front.
    let session = serde_json::json!({
        "run_id": run_id,
        "models": workers,
        "trials": trials,
        "effort_mode": effort_mode,
        "parallel": parallel,
        "models_config": models_file.display().to_string(),
        "tasks_dir": tdir.display().to_string(),
        "task_count": tasks.len(),
        "max_tokens": MAX_TOKENS,
        "run_timeout_secs": RUN_TIMEOUT.as_secs(),
        "grade_timeout_secs": GRADE_TIMEOUT.as_secs(),
        "max_agent_attempts": MAX_AGENT_ATTEMPTS,
    });
    let _ = std::fs::write(
        results_dir.join("session.json"),
        serde_json::to_string_pretty(&session).unwrap_or_default(),
    );

    // Build the full job list (task × model × trial).
    let mut jobs: Vec<Job> = Vec::new();
    for task_dir in &tasks {
        let slug = task_dir.file_name().unwrap().to_string_lossy().to_string();
        let brief = match std::fs::read_to_string(task_dir.join("brief.txt")) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("{slug}: missing brief.txt ({e}); skipping");
                continue;
            }
        };
        let setup = task_dir.join("setup");
        for (worker_name, spec, api_key) in &resolved {
            let model_id = spec.model.clone();
            let max_tokens = spec.max_tokens.unwrap_or(MAX_TOKENS);
            for trial in 1..=trials {
                jobs.push(Job {
                    slug: slug.clone(),
                    brief: brief.clone(),
                    setup: setup.clone(),
                    task_dir: task_dir.clone(),
                    worker_name: worker_name.clone(),
                    spec: spec.clone(),
                    model_id: model_id.clone(),
                    api_key: api_key.clone(),
                    max_tokens,
                    trial,
                });
            }
        }
    }

    // Run jobs through a bounded JoinSet (at most `parallel` in flight).
    let http = Arc::new(http);
    let bench_root = Arc::new(bench_root);
    let results_dir = Arc::new(results_dir);
    let effort_mode = Arc::new(effort_mode);

    let mut set: JoinSet<Outcome> = JoinSet::new();
    let mut jobs = jobs.into_iter();
    let mut outcomes: Vec<Outcome> = Vec::new();

    // Prime up to `parallel` jobs.
    for _ in 0..parallel {
        if let Some(job) = jobs.next() {
            spawn_job(
                &mut set,
                job,
                http.clone(),
                bench_root.clone(),
                results_dir.clone(),
                jsonl.clone(),
                effort_mode.clone(),
                run_id,
            );
        }
    }
    // As each finishes, refill from the queue.
    while let Some(joined) = set.join_next().await {
        match joined {
            Ok(oc) => outcomes.push(oc),
            Err(e) => eprintln!("a run task panicked or was cancelled: {e}"),
        }
        if let Some(job) = jobs.next() {
            spawn_job(
                &mut set,
                job,
                http.clone(),
                bench_root.clone(),
                results_dir.clone(),
                jsonl.clone(),
                effort_mode.clone(),
                run_id,
            );
        }
    }

    let _ = std::fs::remove_dir_all(bench_root.as_path());
    print_report(&outcomes, &workers, &results_dir);
}

#[allow(clippy::too_many_arguments)]
fn spawn_job(
    set: &mut JoinSet<Outcome>,
    job: Job,
    http: Arc<reqwest::Client>,
    bench_root: Arc<PathBuf>,
    results_dir: Arc<PathBuf>,
    jsonl: Arc<PathBuf>,
    effort_mode: Arc<String>,
    run_id: u64,
) {
    set.spawn(async move {
        run_one(
            job,
            &http,
            &bench_root,
            &results_dir,
            &jsonl,
            &effort_mode,
            run_id,
        )
        .await
    });
}

#[allow(clippy::too_many_arguments)]
async fn run_one(
    job: Job,
    http: &reqwest::Client,
    bench_root: &Path,
    results_dir: &Path,
    jsonl: &Path,
    effort_mode: &str,
    run_id: u64,
) -> Outcome {
    let Job {
        slug,
        brief,
        setup,
        task_dir,
        worker_name,
        spec,
        model_id,
        api_key,
        max_tokens,
        trial,
    } = job;

    let run_label = format!("{slug}__{worker_name}__t{trial}");
    let ws = bench_root.join(&run_label);
    eprintln!("▶ {worker_name:>14} :: {slug} (t{trial})");
    let t0 = Instant::now();
    let mut oc = Outcome {
        task: slug.clone(),
        worker: worker_name.clone(),
        trial,
        solved: false,
        score: 0.0,
        passed: 0,
        total: 0,
        import_ok: false,
        observed: false,
        secs: 0.0,
        prompt_tokens: 0,
        cached_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0.0,
        calls: 0,
        note: String::new(),
    };

    let mut attempt = 1;
    let run = loop {
        if let Err(e) = seed_workspace(&ws, &setup, &slug) {
            oc.note = format!("setup copy failed: {e}");
            break None;
        }
        let result = tokio::time::timeout(
            RUN_TIMEOUT,
            run_agent(
                http,
                &spec,
                api_key.as_deref(),
                Some(AGENT_SYSTEM),
                &brief,
                max_tokens,
                &ws,
            ),
        )
        .await;
        match &result {
            Ok(Err(e)) if e.is_transient() && attempt < MAX_AGENT_ATTEMPTS => {
                let delay = retry_delay(e, attempt);
                eprintln!(
                    "  retrying {run_label} after transient agent error \
                     (attempt {attempt}/{MAX_AGENT_ATTEMPTS}): {e}"
                );
                tokio::time::sleep(delay).await;
                attempt += 1;
            }
            _ => break Some(result),
        }
    };
    let secs = t0.elapsed().as_secs_f64();
    oc.secs = secs;

    let run_dir = results_dir.join(&run_label);
    let _ = std::fs::create_dir_all(&run_dir);
    let mut scorecard_raw = String::new();

    match run {
        None => {}
        Some(Err(_)) => oc.note = "run timed out".into(),
        Some(Ok(Err(e))) => {
            oc.note = if attempt > 1 {
                format!("agent error after {attempt} attempts: {e}")
            } else {
                format!("agent error: {e}")
            };
        }
        Some(Ok(Ok(r))) => {
            oc.prompt_tokens = r.prompt_tokens;
            oc.cached_tokens = r.cached_tokens;
            oc.completion_tokens = r.completion_tokens;
            oc.calls = r.trajectory.len();
            // Cache-aware cost from the model spec's rates (any configured model is
            // priced). Falls back to the flat cost if no cached rate is set.
            oc.cost_usd =
                spec.cache_aware_price(r.prompt_tokens, r.cached_tokens, r.completion_tokens);

            // Preserve the model's actual solution + trajectory BEFORE dropping the grader in.
            let _ = copy_dir(&ws, &run_dir.join("workspace"));
            let mut transcript = r.trajectory.join("\n");
            transcript.push_str("\n\n--- final summary ---\n");
            transcript.push_str(&r.text);
            let _ = std::fs::write(run_dir.join("transcript.txt"), &transcript);

            // Drop the hidden grader in only now that the agent has stopped. Observed
            // tasks ship no grader: capture solution + metrics, skip scoring.
            if !task_dir.join("grade.py").exists() {
                oc.observed = true;
                oc.note = "observed (no grader)".into();
            } else if let Err(e) = std::fs::copy(task_dir.join("grade.py"), ws.join("grade.py")) {
                oc.note = format!("grader copy failed: {e}");
            } else {
                match run_grader(&ws) {
                    Ok(GraderRun::TimedOut(o)) => {
                        let stderr = String::from_utf8_lossy(&o.stderr);
                        oc.note = format!(
                            "grader timed out after {}s; stderr: {}",
                            GRADE_TIMEOUT.as_secs(),
                            stderr.lines().last().unwrap_or("")
                        )
                        .chars()
                        .take(120)
                        .collect();
                    }
                    Ok(GraderRun::Completed(o)) => {
                        let stdout = String::from_utf8_lossy(&o.stdout);
                        scorecard_raw = stdout.to_string();
                        // Our graders print one JSON scorecard line and exit 0.
                        let line = stdout
                            .lines()
                            .rev()
                            .find(|l| !l.trim().is_empty())
                            .unwrap_or("");
                        match serde_json::from_str::<serde_json::Value>(line.trim()) {
                            Ok(v) => {
                                oc.score = v["score"].as_f64().unwrap_or(0.0);
                                oc.passed = v["passed"].as_i64().unwrap_or(0);
                                oc.total = v["total"].as_i64().unwrap_or(0);
                                oc.import_ok = v["import_ok"].as_bool().unwrap_or(false);
                                oc.solved = oc.score >= 1.0;
                            }
                            Err(e) => {
                                let err = String::from_utf8_lossy(&o.stderr);
                                oc.note = format!(
                                    "grader output not JSON ({e}); stderr: {}",
                                    err.lines().last().unwrap_or("")
                                )
                                .chars()
                                .take(120)
                                .collect();
                            }
                        }
                    }
                    Err(e) => oc.note = format!("grade run failed: {e}"),
                }
            }
        }
    }

    if !scorecard_raw.is_empty() {
        let _ = std::fs::write(run_dir.join("scorecard.json"), &scorecard_raw);
    }

    let cached_frac = if oc.prompt_tokens > 0 {
        oc.cached_tokens as f64 / oc.prompt_tokens as f64
    } else {
        0.0
    };
    let row = serde_json::json!({
        "run_id": run_id,
        "worker": oc.worker,
        "model": model_id,
        "effort_mode": effort_mode,
        "task": oc.task,
        "trial": oc.trial,
        "secs": oc.secs,
        "prompt_tokens": oc.prompt_tokens,
        "cached_tokens": oc.cached_tokens,
        "cached_frac": cached_frac,
        "completion_tokens": oc.completion_tokens,
        "calls": oc.calls,
        "score": oc.score,
        "passed": oc.passed,
        "total": oc.total,
        "solved": oc.solved,
        "import_ok": oc.import_ok,
        "observed": oc.observed,
        "cost_usd": oc.cost_usd,
        "note": oc.note,
    });
    let _ = std::fs::write(
        run_dir.join("meta.json"),
        serde_json::to_string_pretty(&row).unwrap_or_default(),
    );
    append_jsonl(jsonl, &row);

    eprintln!(
        "  {} {:>14} :: {} (t{})  score={:.3} ({}/{}) {:>6.1}s  {:>7} tok ({:.0}% cached)  ${:.4}  {:>2} calls  {}",
        if oc.solved { "✓" } else { "✗" },
        oc.worker,
        oc.task,
        oc.trial,
        oc.score,
        oc.passed,
        oc.total,
        oc.secs,
        oc.prompt_tokens + oc.completion_tokens,
        cached_frac * 100.0,
        oc.cost_usd,
        oc.calls,
        oc.note
    );

    let _ = std::fs::remove_dir_all(&ws);
    oc
}

fn print_report(outcomes: &[Outcome], workers: &[String], results_dir: &Path) {
    println!("\n## thinkbench runner — per run\n");
    println!(
        "| task | model | t | result | score | latency | tokens | cached | cost | calls | note |"
    );
    println!("|---|---|--:|:--:|--:|--:|--:|--:|--:|--:|---|");
    // Stable ordering for the report (parallel completion is out of order).
    let mut sorted: Vec<&Outcome> = outcomes.iter().collect();
    sorted.sort_by(|a, b| {
        (a.task.as_str(), a.worker.as_str(), a.trial).cmp(&(
            b.task.as_str(),
            b.worker.as_str(),
            b.trial,
        ))
    });
    for o in &sorted {
        let cached_frac = if o.prompt_tokens > 0 {
            o.cached_tokens as f64 / o.prompt_tokens as f64
        } else {
            0.0
        };
        println!(
            "| {} | {} | {} | {} | {:.3} | {:.1}s | {} | {:.0}% | ${:.4} | {} | {} |",
            o.task,
            o.worker,
            o.trial,
            if o.solved { "✓" } else { "✗" },
            o.score,
            o.secs,
            o.prompt_tokens + o.completion_tokens,
            cached_frac * 100.0,
            o.cost_usd,
            o.calls,
            o.note
        );
    }

    println!("\n## per-model totals\n");
    println!("| model | solved | mean score | tokens | cached% | cost | avg latency |");
    println!("|---|:--:|--:|--:|--:|--:|--:|");
    for w in workers {
        let rows: Vec<&Outcome> = outcomes.iter().filter(|o| &o.worker == w).collect();
        if rows.is_empty() {
            continue;
        }
        let solved = rows.iter().filter(|o| o.solved).count();
        let mean_score: f64 = rows.iter().map(|o| o.score).sum::<f64>() / rows.len() as f64;
        let prompt: i64 = rows.iter().map(|o| o.prompt_tokens).sum();
        let cached: i64 = rows.iter().map(|o| o.cached_tokens).sum();
        let toks: i64 = rows
            .iter()
            .map(|o| o.prompt_tokens + o.completion_tokens)
            .sum();
        let cost: f64 = rows.iter().map(|o| o.cost_usd).sum();
        let avg: f64 = rows.iter().map(|o| o.secs).sum::<f64>() / rows.len() as f64;
        let cached_pct = if prompt > 0 {
            cached as f64 / prompt as f64 * 100.0
        } else {
            0.0
        };
        println!(
            "| {} | {}/{} | {:.3} | {} | {:.0}% | ${:.4} | {:.1}s |",
            w,
            solved,
            rows.len(),
            mean_score,
            toks,
            cached_pct,
            cost,
            avg
        );
    }

    println!(
        "\nfull per-run records (workspace + transcript + scorecard) → {}",
        results_dir.display()
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "thinkbench-runner-{name}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[cfg(unix)]
    #[test]
    fn copy_dir_does_not_follow_symlinks() {
        let root = temp_root("symlink");
        let src = root.join("src");
        let dst = root.join("dst");
        let outside = root.join("outside-secret.txt");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("safe.txt"), "safe").unwrap();
        std::fs::write(&outside, "secret").unwrap();
        std::os::unix::fs::symlink(&outside, src.join("leak.txt")).unwrap();

        copy_dir(&src, &dst).unwrap();

        assert_eq!(
            std::fs::read_to_string(dst.join("safe.txt")).unwrap(),
            "safe"
        );
        assert!(!dst.join("leak.txt").exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn grader_timeout_kills_hanging_grade() {
        let root = temp_root("grade-timeout");
        std::fs::write(
            root.join("grade.py"),
            "import time\nprint('start')\ntime.sleep(10)\n",
        )
        .unwrap();

        let start = Instant::now();
        let result = run_grader_with_timeout(&root, Duration::from_millis(50)).unwrap();

        assert!(matches!(result, GraderRun::TimedOut(_)));
        assert!(start.elapsed() < Duration::from_secs(2));
        let _ = std::fs::remove_dir_all(&root);
    }
}
