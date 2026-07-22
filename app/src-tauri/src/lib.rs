mod sidecar;
mod tray;

use tauri::{Emitter, Manager, RunEvent, WindowEvent};

/// Reveal and focus the main window.
///
/// Closing the window only hides the app (the tray is the primary surface), so
/// a permission dialog raised by a wake-word turn would render somewhere the
/// user cannot see it and go unanswered until the confirmation times out into a
/// deny. Called from the frontend on confirm.request.
#[tauri::command]
fn show_window(app: tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
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
