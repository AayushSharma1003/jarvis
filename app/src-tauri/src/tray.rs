//! System tray (Windows/Linux) / menu bar (macOS): the app's primary surface.
//! Closing the window hides it; the tray brings it back; Quit really quits.

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{App, Manager};

pub fn init(app: &App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Open Jarvis", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Jarvis", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;

    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}
