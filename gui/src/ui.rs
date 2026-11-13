//! The window: a top bar (file), the options panel (left), the view
//! (centre), the status bar (bottom), and the settings and log windows.

use std::time::Instant;

use bevy_egui::egui::{self, Align, Color32, Layout, RichText, ScrollArea, Ui, UiBuilder, vec2};
use serde_json::{Value, json};

use crate::app::{GuiApp, Level, WorkerState};
use crate::params::{self, View, Window};
use crate::plots::{self, LinePlot, fmt_g};
use crate::protocol::{OptionKind, OptionSchema};
use crate::views::ViewResult;
use crate::worker;

const ERROR_COLOR: Color32 = Color32::from_rgb(230, 80, 70);
const WARNING_COLOR: Color32 = Color32::from_rgb(230, 170, 50);

/// Draws the whole window for one frame.
pub fn draw(app: &mut GuiApp, ctx: &egui::Context) {
    let mut root = Ui::new(
        ctx.clone(),
        "root".into(),
        UiBuilder::new().layer_id(egui::LayerId::background()).max_rect(ctx.viewport_rect()),
    );
    egui::Panel::top("top_bar").show(&mut root, |ui| top_bar(app, ui));
    egui::Panel::bottom("status_bar").show(&mut root, |ui| status_bar(app, ui));
    egui::Panel::left("options_panel")
        .resizable(true)
        .default_size(330.0)
        .size_range(260.0..=600.0)
        .show(&mut root, |ui| {
            ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| options_panel(app, ui));
        });
    egui::CentralPanel::default().show(&mut root, |ui| central(app, ui));
    settings_window(app, ctx);
    log_window(app, ctx);
    dropped_files(app, ctx);
}

// --- Top bar ------------------------------------------------------------------

fn top_bar(app: &mut GuiApp, ui: &mut Ui) {
    ui.horizontal(|ui| {
        let ready = app.worker_ready();
        if ui.add_enabled(!app.dialog_open(), egui::Button::new("Open…")).on_hover_text("Choose a recording").clicked() {
            app.pick_file();
        }
        let recent = app.settings.recent_files.clone();
        ui.add_enabled_ui(!recent.is_empty(), |ui| {
            ui.menu_button("Recent", |ui| {
                for path in recent {
                    if ui.button(&path).clicked() {
                        app.open_file(path);
                        ui.close();
                    }
                }
            });
        });
        ui.separator();
        let edit = egui::TextEdit::singleline(&mut app.path_input)
            .hint_text("Path of a .dat / .h5 / .hdf5 / .tdms / .sgy file (or drop it on the window)")
            .desired_width(ui.available_width() - 260.0);
        let response = ui.add(edit);
        let enter = response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
        if ui.add_enabled(ready, egui::Button::new("Load")).clicked() || (enter && ready) {
            let path = app.path_input.clone();
            app.open_file(path);
        }
        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
            if ui.button("Log").on_hover_text("What the Python worker printed").clicked() {
                app.show_log = !app.show_log;
            }
            if ui.button("Settings").on_hover_text("Python interpreter, auto-update").clicked() {
                app.show_settings = !app.show_settings;
                app.interpreter_input = app.settings.interpreter.clone();
            }
        });
    });
}

// --- Status bar ---------------------------------------------------------------

fn status_bar(app: &mut GuiApp, ui: &mut Ui) {
    ui.horizontal(|ui| {
        match &app.worker_state {
            WorkerState::Ready => {
                if let Some(h) = &app.hello {
                    status_dot(ui, Color32::from_rgb(80, 190, 90));
                    ui.label(format!(
                        "audace-display {} · invisensing {} · Python {}",
                        h.audace_display, h.invisensing, h.python
                    ))
                    .on_hover_text(format!("{}\nnumpy {}", app.worker_command(), h.numpy));
                }
            }
            WorkerState::Starting { .. } => {
                ui.spinner();
                ui.label(format!("Starting `{}`…", app.worker_command()));
            }
            WorkerState::Failed(_) => {
                status_dot(ui, ERROR_COLOR);
                ui.label("The Python worker is not running.");
                if ui.button("Restart").clicked() {
                    app.restart_worker();
                }
            }
            WorkerState::Stopped => {
                ui.label("Worker stopped.");
            }
        }
        ui.separator();
        if let Some(path) = &app.opening {
            ui.spinner();
            ui.label(format!("Opening {path}…"));
        } else if app.busy() {
            let (fraction, text) = match &app.progress {
                Some(p) => match p.total {
                    Some(t) if t > 0 => (
                        p.done as f32 / t as f32,
                        format!("{} {} / {} pulses", p.stage, group(p.done), group(t)),
                    ),
                    _ => (0.0, format!("{} {}", p.stage, group(p.done))),
                },
                None => (0.0, "computing…".to_string()),
            };
            ui.add(egui::ProgressBar::new(fraction).desired_width(260.0).text(text).animate(true));
            if app.cancelling() {
                ui.label("Stopping…");
            } else if ui.button("Cancel").clicked() {
                app.cancel();
            }
        } else if app.render_pending() {
            ui.label("Waiting for the options to settle…");
        }
    });
}

fn status_dot(ui: &mut Ui, color: Color32) {
    let (rect, _) = ui.allocate_exact_size(vec2(12.0, 12.0), egui::Sense::hover());
    ui.painter().circle_filled(rect.center(), 5.0, color);
}

/// `1234567` -> `1,234,567`.
fn group(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

// --- Options panel ------------------------------------------------------------

fn options_panel(app: &mut GuiApp, ui: &mut Ui) {
    ui.add_space(4.0);
    ui.heading("View");
    let available: Vec<(View, bool, Option<String>)> = View::ALL
        .iter()
        .map(|&v| {
            let a = app.file.as_ref().and_then(|f| f.views.iter().find(|x| x.name == v.cli_name()));
            (v, a.is_none_or(|a| a.available), a.and_then(|a| a.reason.clone()))
        })
        .filter(|(v, _, _)| app.options.contains_key(v) || app.hello.is_none())
        .collect();
    ui.horizontal_wrapped(|ui| {
        for (v, ok, reason) in &available {
            let r = ui.add_enabled(*ok, egui::Button::selectable(app.view == *v, v.label()));
            let r = r.on_hover_text(v.description());
            let r = if let Some(reason) = reason { r.on_disabled_hover_text(reason) } else { r };
            if r.clicked() {
                app.view = *v;
            }
        }
    });
    ui.label(RichText::new(app.view.description()).weak());
    ui.separator();

    if app.file.is_none() {
        ui.label("Open a recording to choose its options.");
        return;
    }
    data_section(app, ui);

    let view = app.view;
    if view == View::Info {
        return;
    }
    let colormaps = app.hello.as_ref().map(|h| h.colormaps.clone()).unwrap_or_default();
    let summary = app.file.as_ref().map(|f| f.summary.clone()).unwrap_or_default();
    let Some(opts) = app.options.get_mut(&view) else { return };
    let groups: Vec<(&'static str, Vec<OptionSchema>)> =
        opts.grouped(view).into_iter().map(|(t, v)| (t, v.into_iter().cloned().collect())).collect();
    for (title, options) in groups {
        egui::CollapsingHeader::new(RichText::new(title).strong()).id_salt((view, title)).default_open(true).show(ui, |ui| {
            egui::Grid::new((view, title, "grid")).num_columns(2).spacing([8.0, 6.0]).striped(false).show(ui, |ui| {
                // The exclusive pairs get one radio each.
                if title == "Positions" && view == View::Fft {
                    fft_positions(opts, ui, &summary);
                    return;
                }
                if title == "Location" && view == View::Inspect {
                    inspect_location(opts, ui, &summary);
                    return;
                }
                for o in &options {
                    option_row(opts, o, ui, &colormaps);
                }
            });
        });
    }
    ui.add_space(8.0);
    ui.horizontal(|ui| {
        let stale = app.is_stale();
        if ui.add_enabled(app.worker_ready(), egui::Button::new(if stale { "Render (changed)" } else { "Render" })).clicked() {
            app.render_now();
        }
        if ui.button("CLI defaults").on_hover_text("Every option of this view back to the CLI's default").clicked()
            && let Some(o) = app.options.get_mut(&view)
        {
            o.reset();
            if view == View::Trace {
                let mid = (summary.positions / 2) as f64 * summary.step_m;
                o.set("position", json!(mid));
            }
            app.zoom.remove(&view);
        }
    });
    if view == View::Scope {
        ui.label(RichText::new("Speed, frame rate and the shown line are set above the plot.").weak());
    }
}

fn data_section(app: &mut GuiApp, ui: &mut Ui) {
    let Some(file) = app.file.clone() else { return };
    let view_has_channel = app.options.get(&app.view).is_some_and(|o| o.has("channel"));
    egui::Grid::new("data_grid").num_columns(2).spacing([8.0, 6.0]).show(ui, |ui| {
        ui.label("Channel");
        ui.add_enabled_ui(view_has_channel, |ui| {
            let shown = app.channel.clone().unwrap_or_else(|| format!("{} (default)", file.default_channel));
            egui::ComboBox::from_id_salt("channel").selected_text(shown).show_ui(ui, |ui| {
                ui.selectable_value(&mut app.channel, None, format!("{} (default)", file.default_channel));
                for c in &file.channels {
                    ui.selectable_value(&mut app.channel, Some(c.clone()), c);
                }
            });
        })
        .response
        .on_disabled_hover_text("The CLI's info always describes the default channel.");
        ui.end_row();

        ui.label("Unit");
        ui.vertical(|ui| {
            let units = app.hello.as_ref().map(|h| h.units.clone()).unwrap_or_default();
            for u in units {
                let avail = file.units.iter().find(|a| a.name == u.name);
                let ok = avail.is_none_or(|a| a.available);
                let text = match (&u.unit, u.name.as_str()) {
                    (_, "rad") => "As stored".to_string(),
                    (Some(unit), _) => format!("{} ({unit})", u.quantity),
                    (None, _) => u.quantity.clone(),
                };
                let r = ui.add_enabled(ok, egui::RadioButton::new(app.unit == u.name, text));
                let r = match &u.flag {
                    Some(flag) => r.on_hover_text(format!("The CLI's {flag}")),
                    None => r.on_hover_text("The samples as the file stores them"),
                };
                let r = match avail.and_then(|a| a.reason.clone()) {
                    Some(reason) => r.on_disabled_hover_text(reason),
                    None => r,
                };
                if r.clicked() {
                    app.unit = u.name.clone();
                }
            }
        });
        ui.end_row();
    });
    ui.separator();
}

fn option_label(ui: &mut Ui, o: &OptionSchema, is_default: bool) {
    let text = params::humanize(&o.dest);
    let rich = if is_default { RichText::new(text) } else { RichText::new(text).strong() };
    ui.label(rich).on_hover_text(format!("{}\n\nCLI: {}", o.help, o.flag));
}

/// One option: a checkbox, a number (optionally "auto"), a choice or text.
fn option_row(opts: &mut params::ViewOptions, o: &OptionSchema, ui: &mut Ui, colormaps: &[String]) {
    let is_default = opts.is_default(&o.dest);
    option_label(ui, o, is_default);
    let current = opts.get(&o.dest).clone();
    let new = match o.kind {
        OptionKind::Flag => {
            let mut b = current.as_bool().unwrap_or(false);
            ui.checkbox(&mut b, "").changed().then(|| json!(b))
        }
        OptionKind::Int | OptionKind::Float => number_edit(ui, o, &current),
        OptionKind::Choice => choice_edit(ui, o, &current),
        OptionKind::Str if o.dest == "cmap" => cmap_edit(ui, &current, colormaps),
        OptionKind::Str => text_edit(ui, o, &current),
    };
    if let Some(v) = new {
        opts.set(&o.dest, v);
    }
    ui.end_row();
}

fn number_edit(ui: &mut Ui, o: &OptionSchema, current: &Value) -> Option<Value> {
    let optional = o.default.is_null() && !o.required;
    let mut out = None;
    ui.horizontal(|ui| {
        let mut set = !current.is_null();
        if optional && ui.checkbox(&mut set, "").on_hover_text("Unchecked: the CLI's default (auto)").changed() {
            out = Some(if set { params::number(o.kind, suggested(o)) } else { Value::Null });
        }
        if set || !optional {
            let mut x = current.as_f64().unwrap_or_else(|| suggested(o));
            let mut dv = egui::DragValue::new(&mut x);
            dv = match o.kind {
                OptionKind::Int => dv.speed(1.0).max_decimals(0).range(int_range(&o.dest)),
                _ => dv.speed(drag_speed(&o.dest)).max_decimals(6),
            };
            if ui.add(dv).changed() {
                out = Some(params::number(o.kind, x));
            }
        } else {
            ui.label(RichText::new("auto").weak());
        }
    });
    out
}

/// The value an optional number starts from when it is switched on.
fn suggested(o: &OptionSchema) -> f64 {
    match o.dest.as_str() {
        "subsample_time" => 1.0,
        "max_pulses" => 100_000.0,
        "fmax" => 100.0,
        "clip_percentile" => 99.0,
        _ => o.default.as_f64().unwrap_or(0.0),
    }
}

fn int_range(dest: &str) -> std::ops::RangeInclusive<f64> {
    match dest {
        // Textures are tiled, but a bin count above this is never on screen.
        "max_time_bins" | "max_space_bins" => 1.0..=16_384.0,
        "index" | "line" | "max_pulses" => 0.0..=f64::MAX,
        _ => 1.0..=f64::MAX,
    }
}

fn drag_speed(dest: &str) -> f64 {
    match dest {
        "start_time" | "duration" => 0.01,
        "speed" => 0.01,
        _ => 0.1,
    }
}

fn choice_edit(ui: &mut Ui, o: &OptionSchema, current: &Value) -> Option<Value> {
    let choices = o.choices.clone().unwrap_or_default();
    let text = |v: &Value| match v {
        Value::Null => "off".to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    let mut selected = current.clone();
    egui::ComboBox::from_id_salt(("choice", &o.dest)).selected_text(text(&selected)).show_ui(ui, |ui| {
        if o.default.is_null() {
            ui.selectable_value(&mut selected, Value::Null, "off");
        }
        for c in &choices {
            ui.selectable_value(&mut selected, c.clone(), text(c));
        }
    });
    (&selected != current).then_some(selected)
}

fn cmap_edit(ui: &mut Ui, current: &Value, colormaps: &[String]) -> Option<Value> {
    let mut selected = current.clone();
    let shown = current.as_str().unwrap_or("auto (CLI default)").to_string();
    egui::ComboBox::from_id_salt("cmap").selected_text(shown).height(400.0).show_ui(ui, |ui| {
        ui.selectable_value(&mut selected, Value::Null, "auto (CLI default)");
        for name in colormaps {
            ui.selectable_value(&mut selected, json!(name), name);
        }
    });
    (&selected != current).then_some(selected)
}

fn text_edit(ui: &mut Ui, o: &OptionSchema, current: &Value) -> Option<Value> {
    let mut s = current.as_str().map(str::to_string).unwrap_or_default();
    let r = ui.add(egui::TextEdit::singleline(&mut s).hint_text("default").desired_width(140.0));
    r.changed().then(|| if s.trim().is_empty() && !o.required { Value::Null } else { json!(s) })
}

fn fft_positions(opts: &mut params::ViewOptions, ui: &mut Ui, summary: &crate::protocol::Summary) {
    #[derive(PartialEq, Clone, Copy)]
    enum Mode {
        Whole,
        List,
        Range,
    }
    let pos = opts.get("position").clone();
    let range = opts.get("position_range").clone();
    let mode = if !pos.is_null() { Mode::List } else if !range.is_null() { Mode::Range } else { Mode::Whole };
    let mut chosen = mode;
    ui.label("Where");
    ui.vertical(|ui| {
        ui.radio_value(&mut chosen, Mode::Whole, "Whole fibre (average)");
        ui.radio_value(&mut chosen, Mode::List, "Positions (m)").on_hover_text("'12.5' or '10,20,30': one curve per position");
        ui.radio_value(&mut chosen, Mode::Range, "Range (m)").on_hover_text("'50:100': averaged");
    });
    ui.end_row();
    if chosen != mode {
        let mid = fmt_g((summary.positions / 2) as f64 * summary.step_m, 6);
        let (p, r) = match chosen {
            Mode::Whole => (Value::Null, Value::Null),
            Mode::List => (json!(mid), Value::Null),
            Mode::Range => (Value::Null, json!(format!("0:{}", fmt_g(summary.length_m, 6)))),
        };
        opts.set("position", p);
        opts.set("position_range", r);
    }
    for dest in ["position", "position_range"] {
        if opts.get(dest).is_null() {
            continue;
        }
        let Some(o) = opts.schema.option(dest).cloned() else { continue };
        option_label(ui, &o, false);
        if let Some(v) = text_edit(ui, &o, &opts.get(dest).clone()) {
            opts.set(dest, if v.is_null() { json!("") } else { v });
        }
        ui.end_row();
    }
}

fn inspect_location(opts: &mut params::ViewOptions, ui: &mut Ui, summary: &crate::protocol::Summary) {
    #[derive(PartialEq, Clone, Copy)]
    enum Mode {
        Middle,
        Index,
        Position,
    }
    let mode = if !opts.get("index").is_null() {
        Mode::Index
    } else if !opts.get("position").is_null() {
        Mode::Position
    } else {
        Mode::Middle
    };
    let mut chosen = mode;
    ui.label("Where");
    ui.vertical(|ui| {
        ui.radio_value(&mut chosen, Mode::Middle, "Middle of the fibre");
        ui.radio_value(&mut chosen, Mode::Index, "Index");
        ui.radio_value(&mut chosen, Mode::Position, "Position (m)");
    });
    ui.end_row();
    if chosen != mode {
        let mid = summary.positions / 2;
        let (i, p) = match chosen {
            Mode::Middle => (Value::Null, Value::Null),
            Mode::Index => (json!(mid), Value::Null),
            Mode::Position => (Value::Null, json!(mid as f64 * summary.step_m)),
        };
        opts.set("index", i);
        opts.set("position", p);
    }
    for dest in ["index", "position"] {
        if opts.get(dest).is_null() {
            continue;
        }
        let Some(o) = opts.schema.option(dest).cloned() else { continue };
        option_label(ui, &o, false);
        let mut x = opts.get(dest).as_f64().unwrap_or(0.0);
        let dv = if o.kind == OptionKind::Int {
            egui::DragValue::new(&mut x).speed(1.0).max_decimals(0).range(0.0..=summary.positions.saturating_sub(1) as f64)
        } else {
            egui::DragValue::new(&mut x).speed(0.5).range(0.0..=summary.length_m).suffix(" m")
        };
        if ui.add(dv).changed() {
            opts.set(dest, params::number(o.kind, x));
        }
        ui.end_row();
    }
}

// --- Central view -------------------------------------------------------------

fn central(app: &mut GuiApp, ui: &mut Ui) {
    notices(app, ui);
    if let WorkerState::Failed(why) = &app.worker_state {
        let why = why.clone();
        ui.add_space(12.0);
        ui.label(RichText::new("The Python worker could not run").heading().color(ERROR_COLOR));
        ui.add_space(6.0);
        ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
            ui.label(RichText::new(why).monospace());
        });
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            if ui.button("Settings…").clicked() {
                app.show_settings = true;
                app.interpreter_input = app.settings.interpreter.clone();
            }
            if ui.button("Restart the worker").clicked() {
                app.restart_worker();
            }
        });
        return;
    }
    let Some(file) = &app.file else {
        ui.centered_and_justified(|ui| {
            ui.label(RichText::new("Open a recording (Open…, a path above, or drop a file on the window).").size(16.0));
        });
        return;
    };
    let name = file.summary.name.clone();
    let view = app.view;

    if let Some((_, e)) = app.errors.get(&view).cloned() {
        let (title, color) = match e.kind.as_str() {
            "audace" => ("The CLI refuses these options", WARNING_COLOR),
            "options" => ("Invalid option", WARNING_COLOR),
            "cancelled" => ("Cancelled", ui.visuals().weak_text_color()),
            "file" => ("File error", ERROR_COLOR),
            _ => ("Error", ERROR_COLOR),
        };
        egui::Frame::group(ui.style()).stroke(egui::Stroke::new(1.0, color)).show(ui, |ui| {
            ui.label(RichText::new(title).strong().color(color));
            ui.label(&e.message);
            if (e.kind == "cancelled" || e.kind == "internal") && ui.button("Render again").clicked() {
                app.render_now();
            }
        });
        if !app.results.contains_key(&view) {
            return;
        }
        ui.add_space(4.0);
    }
    if !app.warnings.is_empty() {
        egui::CollapsingHeader::new(RichText::new(format!("{} warning(s)", app.warnings.len())).color(WARNING_COLOR))
            .id_salt("warnings")
            .show(ui, |ui| {
                for w in &app.warnings {
                    ui.label(RichText::new(w).color(WARNING_COLOR));
                }
            });
    }

    let stale = app.is_stale() && app.results.contains_key(&view);
    if !app.results.contains_key(&view) {
        ui.centered_and_justified(|ui| {
            if app.busy() {
                ui.spinner();
            } else {
                ui.label(format!("{} of {name}: press Render.", view.label()));
            }
        });
        return;
    }
    if stale && !app.busy() && !app.render_pending() && !app.settings.auto_render {
        ui.label(RichText::new("The options changed: press Render to update the view.").color(WARNING_COLOR));
    }

    match view {
        View::Info => info_view(app, ui),
        View::Heatmap => heatmap_view(app, ui),
        View::Bands => bands_view(app, ui),
        View::Fft => fft_view(app, ui),
        View::Trace => trace_view(app, ui),
        View::Inspect => inspect_view(app, ui),
        View::Scope => scope_view(app, ui),
    }
}

fn notices(app: &mut GuiApp, ui: &mut Ui) {
    let mut close = None;
    for (i, n) in app.notices.iter().enumerate() {
        let color = match n.level {
            Level::Error => ERROR_COLOR,
            Level::Warning => WARNING_COLOR,
            Level::Info => ui.visuals().text_color(),
        };
        ui.horizontal(|ui| {
            if ui.small_button("✕").clicked() {
                close = Some(i);
            }
            ui.label(RichText::new(&n.text).color(color));
        });
    }
    if let Some(i) = close {
        app.notices.remove(i);
    }
}

fn title(ui: &mut Ui, text: &str) {
    ui.vertical_centered(|ui| {
        for line in text.lines() {
            ui.label(RichText::new(line).strong());
        }
    });
}

fn info_view(app: &mut GuiApp, ui: &mut Ui) {
    let Some(file) = &app.file else { return };
    let s = &file.summary;
    let text = match app.results.get(&View::Info) {
        Some((_, ViewResult::Info(t))) => t.clone(),
        _ => file.info_text.clone(),
    };
    ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        ui.heading(&s.name);
        egui::Grid::new("summary").num_columns(2).striped(true).spacing([16.0, 4.0]).show(ui, |ui| {
            let mut row = |k: &str, v: String| {
                ui.label(RichText::new(k).strong());
                ui.label(v);
                ui.end_row();
            };
            row("Mode", s.mode.clone());
            if s.content_type != 0 {
                row("Content type", format!("{} ({})", s.content_type_name, s.content_type));
            }
            if let Some(q) = &s.quantity {
                row("Quantity", q.clone());
            }
            row("Gauge length", s.gauge_length_m.map_or("not recorded".into(), |g| format!("{} m", fmt_g(g, 6))));
            row("Trigger frequency", s.trig_frequency.clone());
            row("Line rate used", format!("{} Hz", fmt_g(s.line_rate_hz, 10)));
            row("Time correction", s.time_correction.clone().unwrap_or_else(|| "none recorded".into()));
            row("Start", s.start_time.clone());
            row("Pulses", group(s.num_lines));
            row("Duration", format!("{} s", fmt_g(s.duration_s, 7)));
            row("Positions", format!("{} x {} m = {} m", group(s.positions), fmt_g(s.step_m, 6), fmt_g(s.length_m, 7)));
            if let Some(c) = &s.compression {
                row("Compression", c.clone());
            }
            let channels = file.channels.join(", ");
            row("Channels", format!("{channels} (default {})", file.default_channel));
        });
        ui.add_space(10.0);
        ui.label(RichText::new("audace-display info").strong());
        let mut t = text.as_str();
        ui.add(egui::TextEdit::multiline(&mut t).font(egui::TextStyle::Monospace).desired_width(f32::INFINITY));
    });
}

fn zoom_bar(app: &mut GuiApp, ui: &mut Ui, readout: Option<String>) {
    ui.horizontal(|ui| {
        if let Some(z) = app.zoom.get(&app.view) {
            let text = match (z.d0, z.d1) {
                (Some(d0), Some(d1)) => format!(
                    "Zoom: {}–{} s, {}–{} m",
                    fmt_g(z.t0, 6),
                    fmt_g(z.t1, 6),
                    fmt_g(d0, 6),
                    fmt_g(d1, 6)
                ),
                _ => format!("Zoom: {}–{} s", fmt_g(z.t0, 6), fmt_g(z.t1, 6)),
            };
            ui.label(text);
            if ui.button("Reset zoom").clicked() {
                app.reset_zoom();
            }
        } else {
            ui.label(RichText::new("Scroll to zoom, drag to pan, double-click to reset: the window is recomputed.").weak());
        }
        if let Some(r) = readout {
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                ui.monospace(r);
            });
        }
    });
}

/// Turns plot bounds the user settled on into the zoom window of a view.
fn apply_zoom(app: &mut GuiApp, bounds: [f64; 4], with_distance: bool) {
    let Some(file) = &app.file else { return };
    let s = &file.summary;
    let (t0, t1) = if with_distance { (bounds[1], bounds[3]) } else { (bounds[0], bounds[2]) };
    let t0 = t0.max(0.0);
    let t1 = t1.min(s.duration_s);
    if t1 <= t0 {
        return;
    }
    let (d0, d1) = if with_distance {
        let d0 = bounds[0].max(0.0);
        let d1 = bounds[2].min(s.length_m);
        if d1 <= d0 {
            return;
        }
        (Some(d0), Some(d1))
    } else {
        (None, None)
    };
    app.zoom.insert(app.view, Window { t0, t1, d0, d1 });
}

fn heatmap_view(app: &mut GuiApp, ui: &mut Ui) {
    let Some((_, ViewResult::Heatmap(img))) = app.results.get(&View::Heatmap) else { return };
    title(ui, &img.title);
    let mut nav = app.nav.remove(&View::Heatmap).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let avail = ui.available_size();
    let plot_size = vec2((avail.x - 100.0).max(200.0), (avail.y - 30.0).max(200.0));
    let mut outcome = Default::default();
    ui.horizontal(|ui| {
        outcome = plots::heatmap(ui, "heatmap", img, &mut nav, fit, None, plot_size);
        plots::colorbar(ui, img, plot_size.y);
    });
    let readout = hover_readout(&outcome, &img.label);
    let info = format!("{} x {} bins", group(img.rows as u64), group(img.cols as u64));
    app.nav.insert(View::Heatmap, nav);
    zoom_bar(app, ui, Some(format!("{readout}   ({info})")));
    after_zoomable(app, outcome, true);
}

fn hover_readout(outcome: &plots::PlotOutcome, label: &str) -> String {
    match outcome.hover {
        Some((d, t, Some(v))) => format!("t = {} s   d = {} m   {label} = {}", fmt_g(t, 6), fmt_g(d, 6), fmt_g(v as f64, 5)),
        Some((d, t, None)) => format!("t = {} s   d = {} m", fmt_g(t, 6), fmt_g(d, 6)),
        None => String::new(),
    }
}

fn after_zoomable(app: &mut GuiApp, outcome: plots::PlotOutcome, with_distance: bool) {
    if outcome.reset {
        app.reset_zoom();
    } else if let Some(b) = outcome.new_bounds {
        apply_zoom(app, b, with_distance);
    }
}

fn bands_view(app: &mut GuiApp, ui: &mut Ui) {
    let Some((_, ViewResult::Bands { title: suptitle, panels })) = app.results.get(&View::Bands) else { return };
    title(ui, suptitle);
    let n = panels.len();
    let ncols = if n == 1 { 1 } else if n <= 4 { 2 } else { 3 };
    let nrows = n.div_ceil(ncols);
    let avail = ui.available_size();
    let cell = vec2(avail.x / ncols as f32, (avail.y - 30.0) / nrows as f32);
    let plot_size = vec2((cell.x - 110.0).max(150.0), (cell.y - 30.0).max(120.0));
    let mut nav = app.nav.remove(&View::Bands).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let mut outcome = plots::PlotOutcome::default();
    let mut readout = String::new();
    egui::Grid::new("bands").num_columns(ncols).spacing([6.0, 4.0]).show(ui, |ui| {
        for (i, img) in panels.iter().enumerate() {
            ui.vertical(|ui| {
                ui.label(RichText::new(&img.title).strong());
                ui.horizontal(|ui| {
                    // Panel 0 follows the zoom; the others are linked to it.
                    let mut scratch = crate::app::PlotNav::default();
                    let nav_i = if i == 0 { &mut nav } else { &mut scratch };
                    let o = plots::heatmap(ui, &format!("band{i}"), img, nav_i, fit, Some("bands"), plot_size);
                    if o.hover.is_some() {
                        readout = hover_readout(&o, &img.label);
                    }
                    if i == 0 {
                        outcome.new_bounds = o.new_bounds;
                    }
                    outcome.reset |= o.reset;
                    plots::colorbar(ui, img, plot_size.y);
                });
            });
            if (i + 1) % ncols == 0 {
                ui.end_row();
            }
        }
    });
    app.nav.insert(View::Bands, nav);
    zoom_bar(app, ui, Some(readout));
    after_zoomable(app, outcome, true);
}

fn fft_view(app: &mut GuiApp, ui: &mut Ui) {
    let serial = app.result_serial;
    let Some((_, ViewResult::Fft(r))) = app.results.get(&View::Fft) else { return };
    title(ui, &r.meta.title);
    let mut nav = app.nav.remove(&View::Fft).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let curves: Vec<(Option<&str>, &[f64], &[f64])> =
        r.curves.iter().map(|c| (c.label.as_deref(), c.x.as_slice(), c.y.as_slice())).collect();
    let x1 = r.meta.fmax.unwrap_or_else(|| r.curves.first().and_then(|c| c.x.last().copied()).unwrap_or(r.meta.nyquist));
    let fit_bounds = fit.then(|| {
        let xy: Vec<(&[f64], &[f64])> = curves.iter().map(|c| (c.1, c.2)).collect();
        plots::fit_curves(&xy, 0.0, x1, false)
    });
    let size = ui.available_size() - vec2(0.0, 6.0);
    LinePlot {
        id: format!("fft-{serial}"),
        x_label: format!("Frequency (Hz)   --   Nyquist = {} Hz", fmt_g(r.meta.nyquist.round(), 10)),
        y_label: r.meta.y_label.clone(),
        curves,
        fit_bounds,
        log_y: false,
        marker: None,
    }
    .show(ui, size);
    app.nav.insert(View::Fft, nav);
}

fn trace_view(app: &mut GuiApp, ui: &mut Ui) {
    let Some((_, ViewResult::Trace(r))) = app.results.get(&View::Trace) else { return };
    title(ui, &r.meta.title);
    let mut nav = app.nav.remove(&View::Trace).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let size = ui.available_size() - vec2(0.0, 30.0);
    let outcome = plots::trace(ui, &r.times, &r.lo, &r.hi, &r.meta.label, &mut nav, fit, size);
    let points = format!("{} points", group(r.times.len() as u64));
    app.nav.insert(View::Trace, nav);
    zoom_bar(app, ui, Some(points));
    after_zoomable(app, outcome, false);
}

fn inspect_view(app: &mut GuiApp, ui: &mut Ui) {
    let serial = app.result_serial;
    let Some((_, ViewResult::Inspect(r))) = app.results.get(&View::Inspect) else { return };
    title(ui, &r.meta.title);
    let mut nav = app.nav.remove(&View::Inspect).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let avail = ui.available_size();
    let half = (avail.y - 20.0) / 2.0;
    ui.horizontal(|ui| {
        let wave_size = vec2((avail.x - 200.0).max(200.0), half);
        let fit_wave = fit.then(|| {
            let (t0, t1) = (r.times.first().copied().unwrap_or(0.0), r.times.last().copied().unwrap_or(1.0));
            match r.meta.ylim {
                [Some(lo), Some(hi)] => [t0, lo, t1, hi],
                _ => plots::fit_curves(&[(&r.times, &r.waveform)], t0, t1, false),
            }
        });
        LinePlot {
            id: format!("inspect-wave-{serial}"),
            x_label: "Time from selected start (s)".into(),
            y_label: r.meta.y_label.clone(),
            curves: vec![(None, &r.times, &r.waveform)],
            fit_bounds: fit_wave,
            log_y: false,
            marker: None,
        }
        .show(ui, wave_size);
        egui::Frame::group(ui.style()).show(ui, |ui| {
            ui.set_width(180.0);
            ui.label(RichText::new(&r.meta.stats_text).monospace());
        });
    });
    // The spectrum over f > 0 (and <= fmax), its dominant peak marked, as the CLI draws it.
    let fmax = r.meta.fmax;
    let keep: Vec<usize> =
        (0..r.freqs.len()).filter(|&i| r.freqs[i] > 0.0 && fmax.is_none_or(|m| r.freqs[i] <= m)).collect();
    if keep.is_empty() {
        ui.label("FFT unavailable: signal too short");
    } else {
        let fx: Vec<f64> = keep.iter().map(|&i| r.freqs[i]).collect();
        let fy: Vec<f64> = keep.iter().map(|&i| r.magnitude[i]).collect();
        let peak = (0..fy.len()).fold(0, |best, i| if fy[i] > fy[best] { i } else { best });
        let log = r.meta.fft_log;
        let fit_fft = fit.then(|| plots::fit_curves(&[(&fx, &fy)], fx[0], *fx.last().unwrap(), log));
        let label = r.meta.fft_y_label.clone();
        LinePlot {
            id: format!("inspect-fft-{serial}"),
            x_label: "Frequency (Hz)".into(),
            y_label: label,
            curves: vec![(None, &fx, &fy)],
            fit_bounds: fit_fft,
            log_y: log,
            marker: Some((fx[peak], fy[peak], format!("{} Hz", fmt_g((fx[peak] * 100.0).round() / 100.0, 8)))),
        }
        .show(ui, vec2(avail.x, half));
    }
    app.nav.insert(View::Inspect, nav);
}

fn scope_view(app: &mut GuiApp, ui: &mut Ui) {
    let now = Instant::now();
    let Some(player) = app.scope.as_mut() else {
        ui.label("The oscilloscope session ended (the worker restarted): press Render.");
        return;
    };
    let meta = player.result.meta.clone();
    title(ui, &meta.title);
    ui.horizontal(|ui| {
        let playing = player.playing;
        if ui.button(if playing { "⏸ Pause" } else { "▶ Play" }).clicked() {
            player.set_playing(!playing, now);
        }
        ui.label("Speed");
        let mut speed = player.speed;
        if ui.add(egui::DragValue::new(&mut speed).speed(0.01).range(0.0001..=1000.0).max_decimals(4)).changed() {
            player.set_speed(speed, now);
        }
        ui.label("fps");
        let mut fps = player.fps;
        if ui.add(egui::DragValue::new(&mut fps).speed(1.0).range(1.0..=240.0).max_decimals(0)).changed() {
            player.fps = fps;
        }
        ui.separator();
        let n = meta.n_window.max(1);
        let mut local = if player.playing { player.frame.local } else { player.paused_local };
        let slider = egui::Slider::new(&mut local, 0..=n - 1).text("line in the loop");
        if ui.add_enabled(!player.playing, slider).on_disabled_hover_text("Pause to choose a line").changed() {
            player.paused_local = local;
        }
    });
    let f = &player.frame;
    let readout = format!(
        "line {} / {}   t = {} s   ({} lines/s x {})",
        group(f.line),
        group(meta.line_offset + meta.n_window),
        fmt_g(f.t, 7),
        fmt_g(meta.trig_freq, 10),
        fmt_g(player.speed, 4)
    );
    ui.monospace(readout);
    let [lo, hi] = meta.ylim;
    let (lo, hi) = (lo.unwrap_or(-1.0), hi.unwrap_or(1.0));
    let mut nav = app.nav.remove(&View::Scope).unwrap_or_default();
    let fit = std::mem::take(&mut nav.fit);
    let size = ui.available_size() - vec2(0.0, 6.0);
    LinePlot {
        id: format!("scope-{}", meta.session),
        x_label: "Distance (m)".into(),
        y_label: meta.y_label.clone(),
        curves: vec![(None, &f.x, &f.y)],
        fit_bounds: fit.then_some([0.0, lo, meta.length_m, hi]),
        log_y: false,
        marker: None,
    }
    .show(ui, size);
    app.nav.insert(View::Scope, nav);
}

// --- Windows ------------------------------------------------------------------

fn settings_window(app: &mut GuiApp, ctx: &egui::Context) {
    let mut open = app.show_settings;
    egui::Window::new("Settings").open(&mut open).resizable(true).default_width(560.0).show(ctx, |ui| {
        ui.label(RichText::new("Python interpreter").strong());
        ui.label(
            "The command that runs the Python where audace-display and invisensing are installed: \
             `python3`, `py -3`, or the full path of a virtual environment's interpreter \
             (quote a path with spaces).",
        );
        ui.horizontal(|ui| {
            ui.add(egui::TextEdit::singleline(&mut app.interpreter_input).desired_width(360.0));
            if ui.button("Apply and restart").clicked() {
                let cmd = app.interpreter_input.trim().to_string();
                match worker::split_command(&cmd) {
                    Ok(_) => app.apply_interpreter(cmd),
                    Err(e) => app.notify(Level::Error, e),
                }
            }
            if ui.button("Default").clicked() {
                app.interpreter_input = worker::default_interpreter().to_string();
            }
        });
        ui.label(RichText::new(format!("Runs: {}", app.worker_command())).weak());
        if app.startup.python.is_some() {
            ui.label(RichText::new("--python on the command line overrides the saved interpreter for this run.").weak());
        }
        ui.add_space(6.0);
        ui.label(RichText::new("Install in that Python").strong());
        let cmd = app.interpreter.clone();
        let mut hint = format!(
            "{cmd} -m pip install audace-display\n# or, from a checkout of the audace-display repository:\n{cmd} -m pip install -e <path to audace-display>"
        );
        ui.add(egui::TextEdit::multiline(&mut hint).font(egui::TextStyle::Monospace).desired_rows(3));
        ui.add_space(6.0);
        let mut auto = app.settings.auto_render;
        if ui
            .checkbox(&mut auto, "Update the view automatically when an option changes")
            .on_hover_text("Off: press Render after changing options (large files).")
            .changed()
        {
            app.settings.auto_render = auto;
            app.save_settings();
        }
        if let Some(p) = crate::settings::settings_path() {
            ui.label(RichText::new(format!("Saved in {}", p.display())).weak());
        }
    });
    app.show_settings = open;
}

fn log_window(app: &mut GuiApp, ctx: &egui::Context) {
    let mut open = app.show_log;
    let lines = app.stderr_tail(400);
    egui::Window::new("Worker log").open(&mut open).default_size([700.0, 400.0]).show(ctx, |ui| {
        ui.label(RichText::new(app.worker_command()).weak());
        ScrollArea::vertical().stick_to_bottom(true).auto_shrink([false, false]).show(ui, |ui| {
            if lines.is_empty() {
                ui.label(RichText::new("(nothing written to stderr)").weak());
            }
            for l in &lines {
                ui.monospace(l);
            }
        });
    });
    app.show_log = open;
}

fn dropped_files(app: &mut GuiApp, ctx: &egui::Context) {
    let dropped: Vec<String> = ctx.input(|i| {
        i.raw.dropped_files.iter().map(|f| f.path().display().to_string()).filter(|p| !p.is_empty()).collect()
    });
    if let Some(path) = dropped.into_iter().next() {
        app.open_file(path);
    }
}
