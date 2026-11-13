//! audace-display-gui: a desktop viewer for Audace DAS recordings.
//!
//! The window is a Bevy app drawing an egui interface (bevy_egui). Every
//! number it shows is computed by audace-display itself: a Python worker
//! (`python -m audace_display.gui_worker`) runs the CLI's own views and
//! sends their arrays over a pipe (see `protocol`).

// A release build on Windows opens no console window beside the viewer.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod app;
mod colormap;
mod params;
mod plots;
mod protocol;
mod settings;
mod ui;
mod views;
mod worker;

use std::sync::Mutex;
use std::time::{Duration, Instant};

use bevy::prelude::*;
use bevy::render::view::screenshot::{Screenshot, save_to_disk};
use bevy::window::WindowResolution;
use bevy::winit::{EventLoopProxyWrapper, UpdateMode, WinitSettings, WinitUserEvent};
use bevy_egui::{EguiContexts, EguiPlugin, EguiPrimaryContextPass};

use crate::app::{GuiApp, StartupArgs};
use crate::settings::Settings;

/// Startup values handed to the setup system.
#[derive(Resource)]
struct Boot(Mutex<Option<(Settings, Option<String>, StartupArgs)>>);

fn main() {
    let startup = match StartupArgs::parse(std::env::args().skip(1)) {
        Ok(a) => a,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("error: {e}");
            }
            eprintln!("{}", StartupArgs::USAGE);
            std::process::exit(if e.is_empty() { 0 } else { 2 });
        }
    };
    let (settings, warning) = Settings::load_from(settings::settings_path().as_deref());

    App::new()
        .insert_resource(ClearColor(Color::srgb(0.11, 0.11, 0.12)))
        .add_plugins(DefaultPlugins.set(WindowPlugin {
            primary_window: Some(Window {
                title: "Audace Display".into(),
                resolution: WindowResolution::new(1440, 900),
                ..default()
            }),
            ..default()
        }))
        .add_plugins(EguiPlugin::default())
        // Redraw on input and when the worker wakes the loop up; idle otherwise.
        .insert_resource(WinitSettings {
            focused_mode: UpdateMode::reactive(Duration::from_secs(1)),
            unfocused_mode: UpdateMode::reactive_low_power(Duration::from_secs(5)),
        })
        .insert_resource(Boot(Mutex::new(Some((settings, warning, startup)))))
        .add_systems(Startup, setup)
        .add_systems(EguiPrimaryContextPass, ui_system)
        .add_systems(Update, screenshot_system)
        .run();
}

fn setup(mut commands: Commands, boot: Res<Boot>, proxy: Option<Res<EventLoopProxyWrapper>>) {
    commands.spawn(Camera2d);
    let (settings, warning, startup) = boot
        .0
        .lock()
        .expect("boot lock")
        .take()
        .expect("setup runs once");
    // The worker's reader threads wake the event loop when a message arrives.
    let waker = match proxy {
        Some(p) => {
            let p = Mutex::new((**p).clone());
            app::waker(move || {
                if let Ok(p) = p.lock() {
                    let _ = p.send_event(WinitUserEvent::WakeUp);
                }
            })
        }
        None => app::waker(|| {}),
    };
    let mut gui = GuiApp::new(settings, warning, startup, waker);
    gui.start_worker();
    commands.insert_resource(gui);
}

fn ui_system(mut contexts: EguiContexts, gui: Option<ResMut<GuiApp>>) -> Result {
    let Some(mut gui) = gui else { return Ok(()) };
    let ctx = contexts.ctx_mut()?.clone();
    gui.ctx = ctx.clone();
    gui.update(Instant::now());
    ui::draw(&mut gui, &ctx);
    if gui.animating() {
        ctx.request_repaint();
    }
    Ok(())
}

/// `--screenshot`: once the requested view is drawn, save the window and quit.
fn screenshot_system(
    mut commands: Commands,
    gui: Option<ResMut<GuiApp>>,
    mut exit: MessageWriter<AppExit>,
    mut stage: Local<Option<(u32, std::path::PathBuf)>>,
) {
    let Some(mut gui) = gui else { return };
    let Some(path) = gui.startup.screenshot.clone() else {
        return;
    };
    match stage.as_mut() {
        None => {
            if gui.screenshot_ready() {
                gui.ready_frames += 1;
            } else {
                gui.ready_frames = 0;
            }
            // A few frames for the layout and the textures to settle.
            if gui.ready_frames >= 10 {
                info!("saving the window to {}", path.display());
                commands
                    .spawn(Screenshot::primary_window())
                    .observe(save_to_disk(path.clone()));
                *stage = Some((0, path));
            }
        }
        Some((frames, path)) => {
            *frames += 1;
            if path.exists() || *frames > 600 {
                if !path.exists() {
                    error!("the screenshot was not written");
                }
                exit.write(AppExit::Success);
            }
        }
    }
}
