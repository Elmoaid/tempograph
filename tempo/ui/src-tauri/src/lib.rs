use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Serialize, Deserialize)]
struct TempoResult {
    success: bool,
    output: String,
    mode: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct FileEntry {
    name: String,
    path: String,
    is_dir: bool,
    size: u64,
    modified: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ConfigData {
    success: bool,
    data: serde_json::Value,
    path: String,
}

fn tempo_config_path(repo_path: &str) -> PathBuf {
    Path::new(repo_path).join(".tempo").join("config.json")
}

/// Run a tempo CLI command and return the output.
#[tauri::command]
fn run_tempo(repo_path: String, mode: String, extra_args: Vec<String>) -> TempoResult {
    // Validate repo_path is an existing directory
    if !Path::new(&repo_path).is_dir() {
        return TempoResult {
            success: false,
            output: format!("Directory not found: {}", repo_path),
            mode: mode.clone(),
        };
    }

    let mut cmd = Command::new("python3");
    cmd.arg("-m").arg("tempo").arg(&repo_path).arg("--mode").arg(&mode);
    for arg in &extra_args {
        cmd.arg(arg);
    }

    match cmd.output() {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout).to_string();
            let stderr = String::from_utf8_lossy(&output.stderr).to_string();
            let out = if output.status.success() {
                if stdout.trim().is_empty() { stderr } else { stdout }
            } else {
                if stderr.trim().is_empty() { stdout } else { stderr }
            };
            TempoResult {
                success: output.status.success(),
                output: out,
                mode,
            }
        }
        Err(e) => TempoResult {
            success: false,
            output: format!("Failed to run tempo: {}", e),
            mode,
        },
    }
}

/// Read the .tempo/config.json for a repo.
#[tauri::command]
fn read_config(repo_path: String) -> ConfigData {
    let config_path = tempo_config_path(&repo_path);
    let path_str = config_path.to_string_lossy().to_string();

    if !config_path.exists() {
        return ConfigData {
            success: true,
            data: serde_json::json!({
                "enabled_plugins": [],
                "disabled_plugins": [],
                "max_tokens": 4000,
                "token_budget": "auto",
                "ui_theme": "dark",
                "telemetry": true,
                "learning": true
            }),
            path: path_str,
        };
    }

    match fs::read_to_string(&config_path) {
        Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
            Ok(data) => ConfigData { success: true, data, path: path_str },
            Err(e) => ConfigData {
                success: false,
                data: serde_json::json!({"error": format!("Parse error: {}", e)}),
                path: path_str,
            },
        },
        Err(e) => ConfigData {
            success: false,
            data: serde_json::json!({"error": format!("Read error: {}", e)}),
            path: path_str,
        },
    }
}

/// Write config data to .tempo/config.json.
#[tauri::command]
fn write_config(repo_path: String, config: serde_json::Value) -> ConfigData {
    let config_path = tempo_config_path(&repo_path);
    let path_str = config_path.to_string_lossy().to_string();

    if let Some(parent) = config_path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    match serde_json::to_string_pretty(&config) {
        Ok(json_str) => match fs::write(&config_path, &json_str) {
            Ok(_) => ConfigData { success: true, data: config, path: path_str },
            Err(e) => ConfigData {
                success: false,
                data: serde_json::json!({"error": format!("Write error: {}", e)}),
                path: path_str,
            },
        },
        Err(e) => ConfigData {
            success: false,
            data: serde_json::json!({"error": format!("Serialize error: {}", e)}),
            path: path_str,
        },
    }
}

/// List notes in the notes/ directory.
#[tauri::command]
fn list_notes(repo_path: String) -> Vec<FileEntry> {
    let notes_dir = Path::new(&repo_path).join("notes");
    let mut entries = Vec::new();

    if let Ok(dir) = fs::read_dir(&notes_dir) {
        for entry in dir.flatten() {
            if let Ok(meta) = entry.metadata() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with('.') { continue; }
                entries.push(FileEntry {
                    name,
                    path: entry.path().to_string_lossy().to_string(),
                    is_dir: meta.is_dir(),
                    size: meta.len(),
                    modified: meta.modified().ok().map(|t| {
                        let dur = t.duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
                        format!("{}", dur.as_secs())
                    }),
                });
            }
        }
    }

    entries.sort_by(|a, b| b.modified.cmp(&a.modified));
    entries
}

/// Read a file's contents (max 10MB to prevent OOM).
#[tauri::command]
fn read_file(path: String) -> TempoResult {
    let p = Path::new(&path);
    // Size guard: reject files over 10MB
    if let Ok(meta) = fs::metadata(p) {
        if meta.len() > 10 * 1024 * 1024 {
            return TempoResult {
                success: false,
                output: format!("File too large: {} bytes (max 10MB)", meta.len()),
                mode: "file".into(),
            };
        }
    }
    match fs::read_to_string(&path) {
        Ok(content) => TempoResult { success: true, output: content, mode: "file".into() },
        Err(e) => TempoResult { success: false, output: format!("Error: {}", e), mode: "file".into() },
    }
}

/// Read telemetry data (usage.jsonl + feedback.jsonl).
#[tauri::command]
fn read_telemetry(repo_path: String) -> TempoResult {
    let tempo_dir = Path::new(&repo_path).join(".tempograph");
    let mut output = String::new();

    for file in &["usage.jsonl", "feedback.jsonl"] {
        let path = tempo_dir.join(file);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path) {
                let lines: Vec<&str> = content.lines().collect();
                let recent: Vec<&str> = lines.iter().rev().take(50).copied().collect();
                output.push_str(&format!("=== {} ({} total entries, showing last 50) ===\n", file, lines.len()));
                for line in recent.iter().rev() {
                    output.push_str(line);
                    output.push('\n');
                }
                output.push('\n');
            }
        } else {
            output.push_str(&format!("=== {} (not found) ===\n\n", file));
        }
    }

    // Also check global telemetry
    let global_dir = dirs_path().join("global");
    for file in &["usage.jsonl", "feedback.jsonl"] {
        let path = global_dir.join(file);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path) {
                let lines: Vec<&str> = content.lines().collect();
                let recent: Vec<&str> = lines.iter().rev().take(50).copied().collect();
                output.push_str(&format!("=== global/{} ({} total, showing last 50) ===\n", file, lines.len()));
                for line in recent.iter().rev() {
                    output.push_str(line);
                    output.push('\n');
                }
                output.push('\n');
            }
        }
    }

    TempoResult { success: true, output, mode: "telemetry".into() }
}

fn dirs_path() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join(".tempograph")
    } else {
        PathBuf::from("/tmp/.tempograph")
    }
}

/// Get repo info — quick stats without full graph build.
#[tauri::command]
fn get_repo_info(repo_path: String) -> TempoResult {
    let path = Path::new(&repo_path);
    if !path.exists() {
        return TempoResult { success: false, output: "Path does not exist".into(), mode: "info".into() };
    }
    if !path.is_dir() {
        return TempoResult { success: false, output: "Not a directory".into(), mode: "info".into() };
    }

    let has_git = path.join(".git").exists();
    let has_tempo = path.join(".tempo").exists();
    let has_config = path.join(".tempo").join("config.json").exists();

    let info = serde_json::json!({
        "path": repo_path,
        "has_git": has_git,
        "has_tempo": has_tempo,
        "has_config": has_config,
        "name": path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
    });

    TempoResult { success: true, output: info.to_string(), mode: "info".into() }
}

/// Get git status + recent log for a repo.
#[tauri::command]
fn git_info(repo_path: String) -> TempoResult {
    let mut output = String::new();

    // git status
    if let Ok(status) = Command::new("git")
        .arg("-C").arg(&repo_path)
        .arg("status").arg("--short")
        .output()
    {
        let s = String::from_utf8_lossy(&status.stdout);
        let lines: Vec<&str> = s.lines().collect();
        output.push_str(&format!("=== Status ({} changed) ===\n", lines.len()));
        for line in lines.iter().take(30) {
            output.push_str(line);
            output.push('\n');
        }
        if lines.len() > 30 {
            output.push_str(&format!("... +{} more\n", lines.len() - 30));
        }
        output.push('\n');
    }

    // git branch
    if let Ok(branch) = Command::new("git")
        .arg("-C").arg(&repo_path)
        .arg("branch").arg("--show-current")
        .output()
    {
        let b = String::from_utf8_lossy(&branch.stdout).trim().to_string();
        output.push_str(&format!("Branch: {}\n\n", b));
    }

    // git log
    if let Ok(log) = Command::new("git")
        .arg("-C").arg(&repo_path)
        .arg("log").arg("--oneline").arg("-15")
        .output()
    {
        output.push_str("=== Recent Commits ===\n");
        output.push_str(&String::from_utf8_lossy(&log.stdout));
    }

    TempoResult { success: true, output, mode: "git".into() }
}

/// List files/dirs in a given directory (1 level deep).
#[tauri::command]
fn list_dir(path: String) -> Vec<FileEntry> {
    let dir = Path::new(&path);
    let mut entries = Vec::new();

    if let Ok(rd) = fs::read_dir(dir) {
        for entry in rd.flatten() {
            if entries.len() >= 5000 { break; } // Safety limit
            if let Ok(meta) = entry.metadata() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.starts_with('.') { continue; }
                if meta.is_symlink() { continue; } // Skip symlinks
                entries.push(FileEntry {
                    name,
                    path: entry.path().to_string_lossy().to_string(),
                    is_dir: meta.is_dir(),
                    size: meta.len(),
                    modified: meta.modified().ok().map(|t| {
                        let dur = t.duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
                        format!("{}", dur.as_secs())
                    }),
                });
            }
        }
    }

    entries.sort_by(|a, b| {
        b.is_dir.cmp(&a.is_dir).then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    entries
}

/// Write/create a note file in notes/ directory.
#[tauri::command]
fn write_note(repo_path: String, name: String, content: String) -> TempoResult {
    let notes_dir = Path::new(&repo_path).join("notes");
    let _ = fs::create_dir_all(&notes_dir);
    let file_path = notes_dir.join(&name);

    match fs::write(&file_path, &content) {
        Ok(_) => TempoResult {
            success: true,
            output: file_path.to_string_lossy().to_string(),
            mode: "write_note".into(),
        },
        Err(e) => TempoResult {
            success: false,
            output: format!("Error: {}", e),
            mode: "write_note".into(),
        },
    }
}

/// Save mode output to a file.
#[tauri::command]
fn save_output(path: String, content: String) -> TempoResult {
    if let Some(parent) = Path::new(&path).parent() {
        let _ = fs::create_dir_all(parent);
    }
    match fs::write(&path, &content) {
        Ok(_) => TempoResult { success: true, output: path, mode: "save".into() },
        Err(e) => TempoResult { success: false, output: format!("Error: {}", e), mode: "save".into() },
    }
}

/// Get HOME directory.
#[tauri::command]
fn get_home_dir() -> TempoResult {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    TempoResult { success: true, output: home, mode: "home".into() }
}

/// Write content to a file path (restricted to home dir for safety).
#[tauri::command]
fn write_file(path: String, content: String) -> TempoResult {
    let p = Path::new(&path);

    // Security: only allow writes under the user's home directory
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() || !path.starts_with(&home) {
        return TempoResult {
            success: false,
            output: "Write restricted to home directory".into(),
            mode: "write".into(),
        };
    }

    // Block writes to sensitive paths
    let lower = path.to_lowercase();
    if lower.contains(".ssh") || lower.contains(".gnupg") || lower.contains(".aws") {
        return TempoResult {
            success: false,
            output: "Cannot write to sensitive directories".into(),
            mode: "write".into(),
        };
    }

    if let Some(parent) = p.parent() {
        let _ = fs::create_dir_all(parent);
    }
    match fs::write(&path, &content) {
        Ok(_) => TempoResult { success: true, output: path, mode: "write".into() },
        Err(e) => TempoResult { success: false, output: format!("Error: {}", e), mode: "write".into() },
    }
}

/// Detect git root from current working directory.
#[tauri::command]
fn detect_repo() -> TempoResult {
    let cwd = std::env::current_dir().unwrap_or_default();

    // Walk up looking for .git
    let mut dir = cwd.as_path();
    loop {
        if dir.join(".git").exists() {
            return TempoResult {
                success: true,
                output: dir.to_string_lossy().to_string(),
                mode: "detect".into(),
            };
        }
        match dir.parent() {
            Some(parent) => dir = parent,
            None => break,
        }
    }

    TempoResult {
        success: false,
        output: cwd.to_string_lossy().to_string(),
        mode: "detect".into(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            run_tempo,
            read_config,
            write_config,
            list_notes,
            read_file,
            read_telemetry,
            get_repo_info,
            detect_repo,
            git_info,
            list_dir,
            write_note,
            save_output,
            get_home_dir,
            write_file,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
