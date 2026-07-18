//! Python sidecar supervision: spawn, read the "ready" line, expose
//! {port, token} to the frontend, and guarantee the child never outlives us
//! (kill on exit here + JARVIS_PARENT_PID watchdog on the Python side).
//!
//! Diagnostics: every step of the handshake logs to stderr with a [sidecar]
//! prefix — spawn command, ready parse, event emission, child exit status.
//! Raw stdout echo is gated behind JARVIS_DEBUG=1. The token itself is never
//! logged; a fingerprint (FNV-1a) is, so both ends can be matched up.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use rand::distr::{Alphanumeric, SampleString};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

#[derive(Clone, Serialize)]
pub struct BackendInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Default)]
pub struct SidecarState {
    pub info: Mutex<Option<BackendInfo>>,
    pub child: Mutex<Option<Child>>,
}

#[tauri::command]
pub fn backend_info(state: tauri::State<'_, SidecarState>) -> Option<BackendInfo> {
    let info = state.info.lock().unwrap().clone();
    eprintln!(
        "[sidecar] backend_info queried -> {}",
        match &info {
            Some(i) => format!("port {}", i.port),
            None => "not ready yet".into(),
        }
    );
    info
}

/// Webview console is invisible in a terminal; this routes frontend
/// diagnostics to stderr so handshake failures are debuggable from `tauri dev`
/// output (and from users' bug reports).
#[tauri::command]
pub fn frontend_log(message: String) {
    eprintln!("[frontend] {message}");
}

pub fn token_fingerprint(token: &str) -> String {
    // FNV-1a, enough to correlate logs; not a secret-grade hash on purpose.
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in token.bytes() {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

fn debug_enabled() -> bool {
    std::env::var("JARVIS_DEBUG").is_ok_and(|v| v == "1")
}

pub fn spawn(app: &AppHandle) -> Result<(), String> {
    let token = Alphanumeric.sample_string(&mut rand::rng(), 48);
    let token_fp = token_fingerprint(&token);

    let (mut command, describe) = backend_command(app)?;
    eprintln!(
        "[sidecar] spawning: {describe} (cwd {}), token fp={token_fp}",
        std::env::current_dir()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "?".into())
    );
    let mut child = command
        .env("JARVIS_WS_TOKEN", &token)
        .env("JARVIS_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("failed to spawn backend ({describe}): {e}"))?;
    eprintln!("[sidecar] spawned pid {}", child.id());

    let stdout = child.stdout.take().ok_or("backend stdout unavailable")?;
    let state = app.state::<SidecarState>();
    *state.child.lock().unwrap() = Some(child);

    // Reader thread: first JSON line with event=ready carries the port; the
    // rest of stdout is forwarded to our stderr for debugging. When stdout
    // closes the process is gone — log its exit status and tell the frontend.
    let app = app.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let line = match line {
                Ok(line) => line,
                Err(e) => {
                    eprintln!("[sidecar] stdout read error: {e}");
                    break;
                }
            };
            if debug_enabled() {
                eprintln!("[backend-raw] {line}");
            }
            match parse_ready_port(&line) {
                Ok(port) => {
                    eprintln!("[sidecar] ready parsed: port={port}, token fp={token_fp}");
                    let info = BackendInfo {
                        port,
                        token: token.clone(),
                    };
                    let state = app.state::<SidecarState>();
                    *state.info.lock().unwrap() = Some(info.clone());
                    match app.emit("backend-ready", info) {
                        Ok(()) => eprintln!("[sidecar] backend-ready emitted (port {port})"),
                        Err(e) => eprintln!("[sidecar] backend-ready emit FAILED: {e}"),
                    }
                }
                Err(reason) => {
                    if line.trim_start().starts_with('{') {
                        eprintln!("[sidecar] json line ignored ({reason}): {line}");
                    } else {
                        eprintln!("[backend] {line}");
                    }
                }
            }
        }
        // stdout closed => the child is gone (or closed its pipe). Report how.
        let state = app.state::<SidecarState>();
        let status = state
            .child
            .lock()
            .unwrap()
            .as_mut()
            .and_then(|c| c.try_wait().ok().flatten());
        match status {
            Some(status) => eprintln!("[sidecar] backend exited: {status}"),
            None => eprintln!("[sidecar] backend stdout closed (no exit status yet)"),
        }
        *state.info.lock().unwrap() = None;
        let _ = app.emit("backend-exited", ());
        eprintln!("[sidecar] backend-exited emitted");
    });
    Ok(())
}

pub fn kill(app: &AppHandle) {
    let child = app.state::<SidecarState>().child.lock().unwrap().take();
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
        eprintln!("[sidecar] backend killed on app exit");
    }
}

fn parse_ready_port(line: &str) -> Result<u16, &'static str> {
    let value: serde_json::Value = serde_json::from_str(line).map_err(|_| "not valid JSON")?;
    match value.get("event").and_then(|v| v.as_str()) {
        Some("ready") => {}
        Some(_) => return Err("event != ready"),
        None => return Err("no event field"),
    }
    let port = value
        .get("port")
        .and_then(|p| p.as_u64())
        .ok_or("no numeric port field")?;
    u16::try_from(port).map_err(|_| "port out of range")
}

/// Debug builds run the backend from source via uv; release builds run the
/// PyInstaller onedir bundle shipped in the app resources. Returns the
/// command plus a loggable description of it.
fn backend_command(app: &AppHandle) -> Result<(Command, String), String> {
    if cfg!(debug_assertions) {
        let backend_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../backend")
            .canonicalize()
            .map_err(|e| format!("backend dir not found: {e}"))?;
        let describe = format!(
            "uv --directory {} run jarvis-backend",
            backend_dir.display()
        );
        let mut cmd = Command::new("uv");
        cmd.arg("--directory")
            .arg(backend_dir)
            .arg("run")
            .arg("jarvis-backend");
        Ok((cmd, describe))
    } else {
        let exe = if cfg!(windows) {
            "jarvis-backend.exe"
        } else {
            "jarvis-backend"
        };
        let path = app
            .path()
            .resource_dir()
            .map_err(|e| format!("no resource dir: {e}"))?
            .join("sidecar/jarvis-backend")
            .join(exe);
        if !path.exists() {
            return Err(format!("bundled backend missing at {}", path.display()));
        }
        let describe = path.display().to_string();
        Ok((Command::new(path), describe))
    }
}

#[cfg(test)]
mod tests {
    use super::parse_ready_port;

    #[test]
    fn parses_ready_line() {
        assert_eq!(
            parse_ready_port(r#"{"event": "ready", "port": 54321, "pid": 1}"#),
            Ok(54321)
        );
    }

    #[test]
    fn rejects_non_ready() {
        assert!(parse_ready_port("plain log line").is_err());
        assert!(parse_ready_port(r#"{"event": "other", "port": 1}"#).is_err());
        assert!(parse_ready_port(r#"{"port": 1}"#).is_err());
        assert!(parse_ready_port(r#"{"event": "ready", "port": 99999}"#).is_err());
    }
}
