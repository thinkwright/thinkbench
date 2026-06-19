use std::path::Path;

const DEFAULT_PATH: &str = "/usr/local/bin:/usr/bin:/bin";

fn child_path() -> String {
    std::env::var("PATH")
        .ok()
        .filter(|path| !path.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_PATH.to_string())
}

pub(crate) fn apply_tokio_command_env(cmd: &mut tokio::process::Command, root: &Path) {
    cmd.env_clear();
    cmd.env("PATH", child_path());
    cmd.env("HOME", root);
    cmd.env("LANG", "C.UTF-8");
    cmd.env("LC_ALL", "C.UTF-8");
    cmd.env("PYTHONHASHSEED", "0");
    cmd.env("PYTHONNOUSERSITE", "1");
}

pub(crate) fn apply_std_command_env(cmd: &mut std::process::Command, root: &Path) {
    cmd.env_clear();
    cmd.env("PATH", child_path());
    cmd.env("HOME", root);
    cmd.env("LANG", "C.UTF-8");
    cmd.env("LC_ALL", "C.UTF-8");
    cmd.env("PYTHONHASHSEED", "0");
    cmd.env("PYTHONNOUSERSITE", "1");
}
