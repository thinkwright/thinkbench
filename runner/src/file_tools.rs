//! File + shell tools for grounded and autonomous workers. The runner executes
//! these locally, scoped to the project root, and feeds the results back to the
//! model. `readonly_tools()` is read-only (`read_file`/`grep`/`list_dir`) for
//! grounding; `agent_tools()` adds `write_file` + `run_command` (write/exec) for
//! an autonomous worker that carries a task end to end. Child processes run with
//! a scrubbed environment, but write/exec still require a disposable machine or
//! container; this is not a full sandbox.
//!
//! Ported from lemma's `lemma-worker/src/file_tools.rs` (copied ~as-is; only the
//! audit-log env var was generalized to `THINKBENCH_AUDIT_LOG`).

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::process_env;

/// Cap a tool result so it can't blow the worker's context window.
const MAX_FILE_LINES: usize = 400;
const MAX_GREP_LINES: usize = 120;
const MAX_COMMAND_LINES: usize = 200;
/// A single `run_command` can't block the agent loop longer than this.
const RUN_COMMAND_TIMEOUT: Duration = Duration::from_secs(120);

/// The OpenAI function-calling definitions advertised to a grounded worker.
pub fn readonly_tools() -> serde_json::Value {
    serde_json::json!([
        {"type":"function","function":{
            "name":"read_file",
            "description":"Read a UTF-8 file from the project, numbered by line. Optionally restrict to a line range.",
            "parameters":{"type":"object","properties":{
                "path":{"type":"string","description":"path relative to the project root"},
                "start_line":{"type":"integer","description":"first line, 1-based (optional)"},
                "end_line":{"type":"integer","description":"last line, inclusive (optional)"}
            },"required":["path"]}
        }},
        {"type":"function","function":{
            "name":"grep",
            "description":"Search the project for a regular expression (ripgrep). Returns file:line: text matches.",
            "parameters":{"type":"object","properties":{
                "pattern":{"type":"string","description":"the regex to search for"},
                "glob":{"type":"string","description":"optional file glob to limit the search, e.g. *.go"}
            },"required":["pattern"]}
        }},
        {"type":"function","function":{
            "name":"list_dir",
            "description":"List a directory in the project.",
            "parameters":{"type":"object","properties":{
                "path":{"type":"string","description":"path relative to the project root; omit for the root"}
            },"required":[]}
        }}
    ])
}

/// The full agentic toolkit (read + write + run) for an autonomous worker — the
/// read-only set plus `write_file` and `run_command`. The worker carries a task end
/// to end; the isolated machine + git are the net.
pub fn agent_tools() -> serde_json::Value {
    let mut tools = readonly_tools();
    if let Some(arr) = tools.as_array_mut() {
        arr.push(serde_json::json!({"type":"function","function":{
            "name":"write_file",
            "description":"Create or overwrite a UTF-8 file in the project with the given full content.",
            "parameters":{"type":"object","properties":{
                "path":{"type":"string","description":"path relative to the project root"},
                "content":{"type":"string","description":"the full new file content"}
            },"required":["path","content"]}
        }}));
        arr.push(serde_json::json!({"type":"function","function":{
            "name":"run_command",
            "description":"Run a shell command in the project root (build, run tests, etc.) and return its exit code + stdout/stderr. Use it to verify your work.",
            "parameters":{"type":"object","properties":{
                "command":{"type":"string","description":"the shell command to run"}
            },"required":["command"]}
        }}));
    }
    tools
}

/// Resolve `rel` against `root` and confirm it stays inside `root`. Canonicalizes
/// both sides, so `..` traversal AND symlink escapes are rejected (the symlink's
/// real target is what's checked). The target must exist.
pub fn contain_path(root: &Path, rel: &str) -> Result<PathBuf, String> {
    let root_canon = root
        .canonicalize()
        .map_err(|e| format!("project root error: {e}"))?;
    let canon = root_canon
        .join(rel)
        .canonicalize()
        .map_err(|e| format!("'{rel}': {e}"))?;
    if !canon.starts_with(&root_canon) {
        return Err(format!("'{rel}' escapes the project root"));
    }
    Ok(canon)
}

/// Like [`contain_path`] but for writes: the target need not exist yet. Resolves
/// `rel` against the canonicalized root and rejects anything that escapes it (`..`,
/// absolute paths) or crosses an existing symlink component. Missing tail
/// components are allowed so new files can be created.
pub fn contain_path_for_write(root: &Path, rel: &str) -> Result<PathBuf, String> {
    let root_canon = root
        .canonicalize()
        .map_err(|e| format!("project root error: {e}"))?;
    let mut target = root_canon.clone();
    for comp in Path::new(rel).components() {
        use std::path::Component;
        match comp {
            Component::Normal(c) => {
                target.push(c);
                reject_symlink_component(&target, rel)?;
            }
            Component::CurDir => {}
            Component::ParentDir => {
                target.pop();
                if !target.starts_with(&root_canon) {
                    return Err(format!("'{rel}' escapes the project root"));
                }
            }
            Component::RootDir | Component::Prefix(_) => {
                return Err(format!("'{rel}' must be a relative path"));
            }
        }
    }
    if target == root_canon {
        return Err(format!("'{rel}' is the project root, not a file"));
    }
    Ok(target)
}

fn reject_symlink_component(path: &Path, rel: &str) -> Result<(), String> {
    match std::fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_symlink() => Err(format!(
            "'{rel}' crosses a symlink path component: {}",
            path.display()
        )),
        Ok(_) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("could not inspect '{}': {e}", path.display())),
    }
}

/// Execute one tool call, scoped to `root`. Returns the textual result (or a clear
/// error string the worker can read and adapt to) — never panics, never escapes the
/// root. Covers the read-only set and the agent set (write/exec).
pub async fn execute_tool(root: &Path, name: &str, args: &serde_json::Value) -> String {
    match name {
        "read_file" => read_file(root, args).await,
        "grep" => grep(root, args).await,
        "list_dir" => list_dir(root, args).await,
        "write_file" => write_file(root, args).await,
        "run_command" => run_command(root, args).await,
        other => format!("error: unknown tool '{other}'"),
    }
}

async fn read_file(root: &Path, args: &serde_json::Value) -> String {
    let Some(path) = args.get("path").and_then(|v| v.as_str()) else {
        return "error: read_file requires 'path'".into();
    };
    let resolved = match contain_path(root, path) {
        Ok(p) => p,
        Err(e) => return format!("error: {e}"),
    };
    let content = match tokio::fs::read_to_string(&resolved).await {
        Ok(c) => c,
        Err(e) => return format!("error reading '{path}': {e}"),
    };
    let start = args
        .get("start_line")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;
    let end = args
        .get("end_line")
        .and_then(|v| v.as_u64())
        .map(|e| e as usize);
    let mut out = String::new();
    let mut shown = 0;
    for (i, line) in content.lines().enumerate() {
        let n = i + 1;
        if n < start {
            continue;
        }
        if end.is_some_and(|e| n > e) {
            break;
        }
        if shown >= MAX_FILE_LINES {
            out.push_str(&format!("… (truncated at {MAX_FILE_LINES} lines)\n"));
            break;
        }
        out.push_str(&format!("{n}: {line}\n"));
        shown += 1;
    }
    if out.is_empty() {
        format!("(no lines in range for '{path}')")
    } else {
        out
    }
}

async fn grep(root: &Path, args: &serde_json::Value) -> String {
    let Some(pattern) = args.get("pattern").and_then(|v| v.as_str()) else {
        return "error: grep requires 'pattern'".into();
    };
    // ripgrep, rooted at the project dir (it won't escape its cwd; we pass no
    // paths, only a pattern + optional glob), respecting .gitignore.
    let mut cmd = tokio::process::Command::new("rg");
    cmd.arg("--line-number")
        .arg("--no-heading")
        .arg("--color=never")
        .arg("--max-count=200")
        .current_dir(root);
    process_env::apply_tokio_command_env(&mut cmd, root);
    if let Some(glob) = args.get("glob").and_then(|v| v.as_str()) {
        cmd.arg("--glob").arg(glob);
    }
    cmd.arg("--regexp").arg(pattern);
    let output = match cmd.output().await {
        Ok(o) => o,
        Err(e) => {
            return format!("error: ripgrep unavailable ({e}); use read_file/list_dir instead")
        }
    };
    let text = String::from_utf8_lossy(&output.stdout);
    if text.trim().is_empty() {
        return format!("(no matches for /{pattern}/)");
    }
    let mut out = String::new();
    for (i, line) in text.lines().enumerate() {
        if i >= MAX_GREP_LINES {
            out.push_str(&format!("… (truncated at {MAX_GREP_LINES} matches)\n"));
            break;
        }
        out.push_str(line);
        out.push('\n');
    }
    out
}

async fn list_dir(root: &Path, args: &serde_json::Value) -> String {
    let rel = args.get("path").and_then(|v| v.as_str()).unwrap_or(".");
    let resolved = match contain_path(root, rel) {
        Ok(p) => p,
        Err(e) => return format!("error: {e}"),
    };
    let mut entries = match tokio::fs::read_dir(&resolved).await {
        Ok(e) => e,
        Err(e) => return format!("error listing '{rel}': {e}"),
    };
    let mut names = Vec::new();
    while let Ok(Some(entry)) = entries.next_entry().await {
        let name = entry.file_name().to_string_lossy().to_string();
        let suffix = match entry.file_type().await {
            Ok(ft) if ft.is_dir() => "/",
            _ => "",
        };
        names.push(format!("{name}{suffix}"));
    }
    names.sort();
    if names.is_empty() {
        format!("(empty: '{rel}')")
    } else {
        names.join("\n")
    }
}

async fn write_file(root: &Path, args: &serde_json::Value) -> String {
    let Some(path) = args.get("path").and_then(|v| v.as_str()) else {
        return "error: write_file requires 'path'".into();
    };
    let Some(content) = args.get("content").and_then(|v| v.as_str()) else {
        return "error: write_file requires 'content'".into();
    };
    let resolved = match contain_path_for_write(root, path) {
        Ok(p) => p,
        Err(e) => return format!("error: {e}"),
    };
    if let Some(parent) = resolved.parent() {
        if let Err(e) = tokio::fs::create_dir_all(parent).await {
            return format!("error creating dirs for '{path}': {e}");
        }
    }
    let resolved = match contain_path_for_write(root, path) {
        Ok(p) => p,
        Err(e) => return format!("error: {e}"),
    };
    match tokio::fs::write(&resolved, content).await {
        Ok(()) => format!("wrote {} bytes to '{path}'", content.len()),
        Err(e) => format!("error writing '{path}': {e}"),
    }
}

async fn run_command(root: &Path, args: &serde_json::Value) -> String {
    let Some(command) = args.get("command").and_then(|v| v.as_str()) else {
        return "error: run_command requires 'command'".into();
    };
    audit_run_command(root, command);
    let mut cmd = tokio::process::Command::new("/bin/sh");
    cmd.arg("-c").arg(command).current_dir(root);
    process_env::apply_tokio_command_env(&mut cmd, root);
    let fut = cmd.output();
    let output = match tokio::time::timeout(RUN_COMMAND_TIMEOUT, fut).await {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => return format!("error running command: {e}"),
        Err(_) => {
            return format!(
                "error: command timed out after {}s",
                RUN_COMMAND_TIMEOUT.as_secs()
            )
        }
    };
    let code = output
        .status
        .code()
        .map(|c| c.to_string())
        .unwrap_or_else(|| "signal".into());
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let mut out = format!("exit: {code}\n");
    append_command_output(&mut out, &combined);
    out
}

fn audit_run_command(root: &Path, command: &str) {
    let Some(path) = std::env::var_os("THINKBENCH_AUDIT_LOG") else {
        return;
    };
    let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    else {
        return;
    };
    let _ = writeln!(
        file,
        "{}  INFO   worker run_command cwd={:?} command={:?}",
        unix_millis(),
        root,
        clip_for_log(command, 1000)
    );
}

fn unix_millis() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis())
}

fn clip_for_log(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        return s.to_string();
    }
    let mut out: String = s.chars().take(max_chars).collect();
    out.push_str("...");
    out
}

fn append_command_output(out: &mut String, text: &str) {
    let lines: Vec<_> = text.lines().collect();
    if lines.len() <= MAX_COMMAND_LINES {
        for line in lines {
            out.push_str(line);
            out.push('\n');
        }
        return;
    }

    let head_lines = MAX_COMMAND_LINES / 2;
    let tail_lines = MAX_COMMAND_LINES - head_lines;
    let omitted_lines = lines.len() - head_lines - tail_lines;

    for line in &lines[..head_lines] {
        out.push_str(line);
        out.push('\n');
    }
    out.push_str(&format!(
        "… (truncated {omitted_lines} middle lines; showing first {head_lines} and last {tail_lines} of {} lines)\n",
        lines.len()
    ));
    for line in &lines[lines.len() - tail_lines..] {
        out.push_str(line);
        out.push('\n');
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "thinkbench-file-tools-{name}-{}-{}",
            std::process::id(),
            unix_millis()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[tokio::test]
    async fn run_command_does_not_inherit_provider_secrets() {
        let root = temp_root("env");
        std::env::set_var("THINKBENCH_SECRET_CANARY", "should-not-appear");

        let out = execute_tool(
            &root,
            "run_command",
            &serde_json::json!({
                "command": "printf '%s' \"${THINKBENCH_SECRET_CANARY-unset}\""
            }),
        )
        .await;

        std::env::remove_var("THINKBENCH_SECRET_CANARY");
        let _ = std::fs::remove_dir_all(&root);

        assert!(out.contains("exit: 0"));
        assert!(out.contains("unset"));
        assert!(!out.contains("should-not-appear"));
    }
}
