//! Python sidecar supervision: spawn, read the "ready" line, expose
//! {port, token} to the frontend, and guarantee the child never outlives us
//! (kill on exit here + JARVIS_PARENT_PID watchdog on the Python side).

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
    state.info.lock().unwrap().clone()
}

pub fn spawn(app: &AppHandle) -> Result<(), String> {
    let token = Alphanumeric.sample_string(&mut rand::rng(), 48);

    let mut command = backend_command(app)?;
    let mut child = command
        .env("JARVIS_WS_TOKEN", &token)
        .env("JARVIS_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("failed to spawn backend: {e}"))?;

    let stdout = child.stdout.take().ok_or("backend stdout unavailable")?;
    let state = app.state::<SidecarState>();
    *state.child.lock().unwrap() = Some(child);

    // Reader thread: first JSON line with event=ready carries the port; the
    // rest of stdout is forwarded to our stderr for debugging. When stdout
    // closes the process is gone — tell the frontend.
    let app = app.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if let Some(port) = parse_ready_port(&line) {
                let info = BackendInfo {
                    port,
                    token: token.clone(),
                };
                let state = app.state::<SidecarState>();
                *state.info.lock().unwrap() = Some(info.clone());
                let _ = app.emit("backend-ready", info);
            } else {
                eprintln!("[backend] {line}");
            }
        }
        let state = app.state::<SidecarState>();
        *state.info.lock().unwrap() = None;
        let _ = app.emit("backend-exited", ());
    });
    Ok(())
}

pub fn kill(app: &AppHandle) {
    let child = app.state::<SidecarState>().child.lock().unwrap().take();
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn parse_ready_port(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    if value.get("event")?.as_str()? != "ready" {
        return None;
    }
    u16::try_from(value.get("port")?.as_u64()?).ok()
}

/// Debug builds run the backend from source via uv; release builds run the
/// PyInstaller onedir bundle shipped in the app resources.
fn backend_command(app: &AppHandle) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        let backend_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../backend")
            .canonicalize()
            .map_err(|e| format!("backend dir not found: {e}"))?;
        let mut cmd = Command::new("uv");
        cmd.arg("--directory")
            .arg(backend_dir)
            .arg("run")
            .arg("jarvis-backend");
        Ok(cmd)
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
        Ok(Command::new(path))
    }
}
