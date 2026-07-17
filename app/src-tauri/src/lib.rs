mod sidecar;
mod tray;

use tauri::{RunEvent, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![sidecar::backend_info])
        .setup(|app| {
            tray::init(app)?;
            sidecar::spawn(app.handle())?;
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
