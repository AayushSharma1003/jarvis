mod sidecar;
mod tray;

use tauri::{Emitter, RunEvent, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![
            sidecar::backend_info,
            sidecar::frontend_log
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
