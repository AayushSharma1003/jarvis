mod sidecar;
mod tray;

use tauri::{Emitter, Manager, RunEvent, WindowEvent};

/// Reveal and focus the main window.
///
/// Closing the window only hides the app (the tray is the primary surface), so
/// a permission dialog raised by a wake-word turn would render somewhere the
/// user cannot see it and go unanswered until the confirmation times out into a
/// deny. Called from the frontend on confirm.request.
///
/// Failures are logged rather than discarded. A hidden window has exactly one
/// other way back — the tray icon — because the app has no Dock icon,
/// activating it does not re-show the window, and the Window menu carries no
/// entry for it (all three verified on the packaged build). So if this fails
/// the user is left with an invisible dialog and no hint that one exists; a
/// log line is the only thread back to the cause.
#[tauri::command]
fn show_window(app: tauri::AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        eprintln!("[window] show_window: no webview window named \"main\"");
        return;
    };
    if let Err(e) = window.show() {
        eprintln!("[window] show failed: {e}");
    }
    if let Err(e) = window.unminimize() {
        eprintln!("[window] unminimize failed: {e}");
    }
    if let Err(e) = window.set_focus() {
        eprintln!("[window] set_focus failed: {e}");
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            sidecar::backend_info,
            sidecar::frontend_log,
            show_window
        ])
        .setup(|app| {
            tray::init(app)?;
            // A spawn failure must not abort setup (that would kill the window
            // and hide the error); surface it and let the UI show its error state.
            if let Err(e) = sidecar::spawn(app.handle()) {
                eprintln!("[sidecar] SPAWN FAILED: {e}");
                let _ = app.handle().emit("backend-exited", ());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Tray is the primary surface: closing the window hides the app.
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                sidecar::kill(app);
            }
        });
}
