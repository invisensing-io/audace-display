//! The viewer's state and everything it does besides drawing: the worker's
//! life cycle, requests (one render at a time, superseded ones cancelled),
//! results, the oscilloscope's playback.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bevy::prelude::Resource;
use bevy_egui::egui;
use serde_json::{Map, Value, json};

use crate::i18n::{Lang, Msg, Text, WorkerText};
use crate::params::{self, View, ViewOptions, Window};
use crate::protocol::{self, FileDescription, Hello, Message, Progress, WorkerError};
use crate::settings::{self, Settings};
use crate::views::{self, ScopeFrame, ScopeResult, ViewResult};
use crate::worker::{self, Waker, WorkerEvent, WorkerProcess};

/// Quiet time after the last change of an option before the view is
/// recomputed (typing a number, dragging a value, zooming with the wheel).
const DEBOUNCE: Duration = Duration::from_millis(350);
/// A cancelled request that has not stopped by then has its worker restarted
/// (a long numpy call cannot be interrupted between two chunks).
const CANCEL_GRACE: Duration = Duration::from_secs(5);
/// The worker must answer `hello` within this time.
const HELLO_TIMEOUT: Duration = Duration::from_secs(60);

/// What the command line asked for.
#[derive(Debug, Default, Clone)]
pub struct StartupArgs {
    pub file: Option<String>,
    pub view: Option<View>,
    pub python: Option<String>,
    pub screenshot: Option<PathBuf>,
    pub sets: Vec<(String, String)>,
    pub unit: Option<String>,
    pub zoom: Option<Window>,
    /// The language of this run (not saved).
    pub lang: Option<Lang>,
    /// Open the Settings window at start.
    pub settings: bool,
}

impl StartupArgs {
    pub fn parse(args: impl IntoIterator<Item = String>) -> Result<StartupArgs, String> {
        let mut out = StartupArgs::default();
        let mut it = args.into_iter();
        while let Some(a) = it.next() {
            let mut value = |name: &str| it.next().ok_or_else(|| format!("{name} needs a value"));
            match a.as_str() {
                "--python" => out.python = Some(value("--python")?),
                "--view" => {
                    let v = value("--view")?;
                    out.view =
                        Some(View::from_cli(&v).ok_or_else(|| format!("unknown view '{v}'"))?);
                }
                "--screenshot" => out.screenshot = Some(PathBuf::from(value("--screenshot")?)),
                "--unit" => out.unit = Some(value("--unit")?),
                "--lang" => {
                    let l = value("--lang")?;
                    out.lang = Some(
                        Lang::parse(&l).ok_or_else(|| format!("--lang is en or fr, got '{l}'"))?,
                    );
                }
                "--settings" => out.settings = true,
                "--zoom" => {
                    let z = value("--zoom")?;
                    out.zoom =
                        Some(parse_zoom(&z).ok_or_else(|| {
                            format!("--zoom takes T0:T1 or T0:T1:D0:D1, got '{z}'")
                        })?);
                }
                "--set" => {
                    let kv = value("--set")?;
                    let (k, v) = kv
                        .split_once('=')
                        .ok_or_else(|| format!("--set takes dest=value, got '{kv}'"))?;
                    out.sets.push((k.trim().to_string(), v.trim().to_string()));
                }
                "-h" | "--help" => return Err(String::new()),
                s if s.starts_with('-') => return Err(format!("unknown option '{s}'")),
                s => {
                    if out.file.replace(s.to_string()).is_some() {
                        return Err("one file at a time".into());
                    }
                }
            }
        }
        Ok(out)
    }

    pub const USAGE: &'static str = "usage: audace-display-gui [FILE] [--python CMD] [--lang en|fr] \
[--view VIEW] [--unit UNIT] [--set DEST=VALUE]... [--zoom T0:T1[:D0:D1]] [--settings] \
[--screenshot OUT.png]\n\
  FILE            a recording to open (.dat / .h5 / .hdf5 / .tdms / .sgy)\n\
  --python CMD    the Python interpreter for this run (default: the saved setting)\n\
  --lang en|fr    the language of the window for this run (default: the saved setting,\n\
                  else the system's: French when its locale starts with fr)\n\
  --view VIEW     info, heatmap, bandheatmaps, fft, trace, scope or inspect\n\
  --unit UNIT     rad, nanostrain or strain_rate (when the file allows it)\n\
  --set DEST=V    an option of the view, by its CLI name (max_time_bins=500)\n\
  --zoom T0:T1[:D0:D1]  open the view zoomed on seconds T0..T1 (and metres D0..D1)\n\
  --settings      open the Settings window at start\n\
  --screenshot P  save the window to P once the view is drawn, then quit";
}

fn parse_zoom(text: &str) -> Option<Window> {
    let v: Vec<f64> = text
        .split(':')
        .map(|s| s.trim().parse::<f64>())
        .collect::<Result<_, _>>()
        .ok()?;
    match v[..] {
        [t0, t1] if t1 > t0 => Some(Window {
            t0,
            t1,
            d0: None,
            d1: None,
        }),
        [t0, t1, d0, d1] if t1 > t0 && d1 > d0 => Some(Window {
            t0,
            t1,
            d0: Some(d0),
            d1: Some(d1),
        }),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum WorkerState {
    Stopped,
    Starting { since: Instant },
    Ready,
    Failed(Text),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Error,
    Warning,
    Info,
}

#[derive(Debug, Clone)]
pub struct Notice {
    pub level: Level,
    pub text: Text,
}

enum Inflight {
    Hello,
    Open,
    Render {
        view: View,
        key: String,
        superseded: bool,
        cancelled_at: Option<Instant>,
    },
    Frame,
}

/// A render the viewer wants: the request fields and their identity.
#[derive(Debug, Clone, PartialEq)]
pub struct RenderSpec {
    pub view: View,
    pub key: String,
    pub fields: Map<String, Value>,
}

/// Navigation of a zoomable plot: the bounds it should show.
#[derive(Debug, Default, Clone)]
pub struct PlotNav {
    /// Fit the bounds to the next result (a fresh, unzoomed render).
    pub fit: bool,
    /// The bounds last seen settled, `[x0, y0, x1, y1]`.
    pub expected: Option<[f64; 4]>,
}

/// The oscilloscope being played.
pub struct ScopePlayer {
    pub result: ScopeResult,
    pub frame: ScopeFrame,
    pub playing: bool,
    pub speed: f64,
    pub fps: f64,
    anchor: Instant,
    anchor_local: f64,
    in_flight: bool,
    last_request: Option<Instant>,
    /// The line shown while paused (the slider).
    pub paused_local: u64,
}

impl ScopePlayer {
    fn new(result: ScopeResult, first: ScopeFrame, now: Instant) -> ScopePlayer {
        let (speed, fps) = (result.meta.speed, result.meta.fps);
        ScopePlayer {
            paused_local: first.local,
            anchor_local: first.local as f64,
            frame: first,
            result,
            playing: true,
            speed,
            fps,
            anchor: now,
            in_flight: false,
            last_request: None,
        }
    }

    /// The line of the window to show now: the CLI's clock, `elapsed x
    /// actual pulse rate x speed`, looping over the window.
    pub fn local_now(&self, now: Instant) -> u64 {
        let span = self.result.meta.n_window.max(1) as f64;
        let pos = self.anchor_local
            + now.duration_since(self.anchor).as_secs_f64()
                * self.result.meta.trig_freq
                * self.speed;
        (pos.rem_euclid(span)) as u64
    }

    pub fn set_playing(&mut self, playing: bool, now: Instant) {
        if playing == self.playing {
            return;
        }
        if playing {
            self.anchor = now;
            self.anchor_local = self.paused_local as f64;
        } else {
            self.paused_local = self.local_now(now);
        }
        self.playing = playing;
    }

    pub fn set_speed(&mut self, speed: f64, now: Instant) {
        if self.playing {
            self.anchor_local = self.local_now(now) as f64;
            self.anchor = now;
        }
        self.speed = speed;
    }
}

#[derive(Resource)]
pub struct GuiApp {
    /// The language of the window now.
    pub lang: Lang,
    /// The language of the system's locale.
    pub system_lang: Lang,
    pub settings: Settings,
    settings_path: Option<PathBuf>,
    pub startup: StartupArgs,
    waker: Waker,
    pub ctx: egui::Context,

    worker: Option<WorkerProcess>,
    pub worker_state: WorkerState,
    pub hello: Option<Hello>,
    /// The interpreter in use (the setting, or `--python`).
    pub interpreter: String,
    inflight: BTreeMap<u64, Inflight>,

    pub options: BTreeMap<View, ViewOptions>,
    pub file: Option<FileDescription>,
    pub opening: Option<String>,
    pub path_input: String,
    pub view: View,
    pub channel: Option<String>,
    pub unit: String,

    pub results: BTreeMap<View, (String, ViewResult)>,
    pub errors: BTreeMap<View, (String, WorkerError)>,
    pub warnings: Vec<WorkerText>,
    pub zoom: BTreeMap<View, Window>,
    pub nav: BTreeMap<View, PlotNav>,
    pub progress: Option<Progress>,
    current_render: Option<u64>,
    pending: Option<(String, Instant)>,
    force_render: bool,
    pub result_serial: u64,

    pub scope: Option<ScopePlayer>,
    pub notices: Vec<Notice>,
    dialog: Option<Mutex<Receiver<Option<PathBuf>>>>,

    pub show_settings: bool,
    pub show_log: bool,
    pub interpreter_input: String,
    /// Frames drawn since the requested screenshot view became ready.
    pub ready_frames: u32,
}

impl GuiApp {
    pub fn new(
        settings: Settings,
        settings_warning: Option<Text>,
        startup: StartupArgs,
        waker: Waker,
    ) -> GuiApp {
        let interpreter = startup
            .python
            .clone()
            .unwrap_or_else(|| settings.interpreter.clone());
        let mut notices = Vec::new();
        if let Some(w) = settings_warning {
            notices.push(Notice {
                level: Level::Warning,
                text: w,
            });
        }
        let system_lang = Lang::system();
        GuiApp {
            lang: startup.lang.or(settings.language).unwrap_or(system_lang),
            system_lang,
            show_settings: startup.settings,
            interpreter_input: settings.interpreter.clone(),
            path_input: startup.file.clone().unwrap_or_default(),
            settings,
            settings_path: settings::settings_path(),
            startup,
            waker,
            ctx: egui::Context::default(),
            worker: None,
            worker_state: WorkerState::Stopped,
            hello: None,
            interpreter,
            inflight: BTreeMap::new(),
            options: BTreeMap::new(),
            file: None,
            opening: None,
            view: View::Info,
            channel: None,
            unit: "rad".into(),
            results: BTreeMap::new(),
            errors: BTreeMap::new(),
            warnings: Vec::new(),
            zoom: BTreeMap::new(),
            nav: BTreeMap::new(),
            progress: None,
            current_render: None,
            pending: None,
            force_render: false,
            result_serial: 0,
            scope: None,
            notices,
            dialog: None,
            show_log: false,
            ready_frames: 0,
        }
    }

    pub fn notify(&mut self, level: Level, text: impl Into<Text>) {
        let text = text.into();
        if !self.notices.iter().any(|n| n.text == text) {
            self.notices.push(Notice { level, text });
        }
    }

    pub fn save_settings(&mut self) {
        if let Err(e) = self.settings.save_to(self.settings_path.as_deref()) {
            self.notify(
                Level::Warning,
                Text::from(Msg::SettingsNotSaved).arg("error", e),
            );
        }
    }

    /// The language chosen in Settings (`None`: the system's), at once and
    /// for the next runs.
    pub fn set_language(&mut self, language: Option<Lang>) {
        self.settings.language = language;
        self.lang = language.unwrap_or(self.system_lang);
        self.save_settings();
    }

    // --- The worker ------------------------------------------------------------

    pub fn start_worker(&mut self) {
        self.worker = None;
        self.inflight.clear();
        self.current_render = None;
        self.progress = None;
        if let Some(p) = self.opening.take() {
            self.startup.file = Some(p);
        }
        // The oscilloscope's session lived in the old worker.
        self.scope = None;
        self.results.remove(&View::Scope);
        self.hello = None;
        let mut spawned = WorkerProcess::spawn(&self.interpreter, self.waker.clone());
        if let (Err(_), Some(fallback)) =
            (&spawned, worker::fallback_interpreter(&self.interpreter))
        {
            spawned = WorkerProcess::spawn(fallback, self.waker.clone());
            if spawned.is_ok() {
                self.interpreter = fallback.to_string();
            }
        }
        match spawned {
            Ok(mut w) => {
                let mut fields = Map::new();
                fields.insert("protocol".into(), json!(protocol::PROTOCOL));
                match w.request("hello", fields) {
                    Ok(id) => {
                        self.inflight.insert(id, Inflight::Hello);
                        self.worker_state = WorkerState::Starting {
                            since: Instant::now(),
                        };
                        self.worker = Some(w);
                    }
                    Err(e) => self.worker_failed(e),
                }
            }
            Err(e) => self.worker_failed(e.then(install_hint(&self.interpreter))),
        }
    }

    fn worker_failed(&mut self, why: Text) {
        self.worker = None;
        self.inflight.clear();
        self.current_render = None;
        self.progress = None;
        self.worker_state = WorkerState::Failed(why);
    }

    /// Applies a new interpreter command and restarts the worker.
    pub fn apply_interpreter(&mut self, command: String) {
        self.settings.interpreter = command.clone();
        self.interpreter = command;
        self.save_settings();
        self.restart_worker();
    }

    pub fn restart_worker(&mut self) {
        // Results stay on screen; anything in flight is asked again.
        self.errors.clear();
        self.start_worker();
    }

    pub fn worker_ready(&self) -> bool {
        self.worker_state == WorkerState::Ready && self.worker.is_some()
    }

    pub fn stderr_tail(&self, lines: usize) -> Vec<String> {
        self.worker
            .as_ref()
            .map(|w| w.stderr_tail(lines))
            .unwrap_or_default()
    }

    pub fn worker_command(&self) -> String {
        self.worker
            .as_ref()
            .map(|w| w.command_line.clone())
            .unwrap_or_else(|| format!("{} -m {}", self.interpreter, worker::WORKER_MODULE))
    }

    fn request(&mut self, cmd: &str, fields: Map<String, Value>, what: Inflight) -> Option<u64> {
        let worker = self.worker.as_mut()?;
        match worker.request(cmd, fields) {
            Ok(id) => {
                self.inflight.insert(id, what);
                Some(id)
            }
            Err(e) => {
                let tail = worker.stderr_tail(12).join("\n");
                self.worker_failed(if tail.is_empty() {
                    e
                } else {
                    e.with_detail(tail)
                });
                None
            }
        }
    }

    /// One frame of work: worker events, timers, requests.
    pub fn update(&mut self, now: Instant) {
        let events = self.worker.as_ref().map(|w| w.drain()).unwrap_or_default();
        for ev in events {
            match ev {
                WorkerEvent::Message(m) => self.on_message(m, now),
                WorkerEvent::Stopped(why) => {
                    let why = if matches!(self.worker_state, WorkerState::Starting { .. }) {
                        why.then(install_hint(&self.interpreter))
                    } else {
                        why
                    };
                    self.worker_failed(why);
                }
            }
        }
        if let WorkerState::Starting { since } = self.worker_state
            && now.duration_since(since) > HELLO_TIMEOUT
        {
            self.worker_failed(
                Text::from(Msg::HelloTimeout)
                    .arg("command", self.worker_command())
                    .arg("seconds", HELLO_TIMEOUT.as_secs().to_string()),
            );
        }
        self.check_cancel_grace(now);
        self.poll_dialog();
        self.maybe_render(now);
        self.tick_scope(now);
    }

    fn check_cancel_grace(&mut self, now: Instant) {
        let stuck = self.inflight.values().any(|w| {
            matches!(w, Inflight::Render { cancelled_at: Some(t), .. } if now.duration_since(*t) > CANCEL_GRACE)
        });
        if stuck {
            self.notify(Level::Info, Msg::CancelStuck);
            self.start_worker();
        }
    }

    fn on_message(&mut self, mut msg: Message, now: Instant) {
        let Some(id) = msg.id() else {
            if msg.kind() == "error" {
                let e: Result<WorkerError, _> = msg.parse();
                let text = e
                    .map(|e| Text::Worker(e.text()))
                    .unwrap_or_else(|e| e.text());
                self.notify(
                    Level::Error,
                    Text::from(Msg::WorkerSays).arg("message", text),
                );
            }
            return;
        };
        if msg.kind() == "progress" {
            if Some(id) == self.current_render
                && let Ok(p) = msg.parse::<Progress>()
            {
                self.progress = Some(p);
            }
            return;
        }
        let Some(what) = self.inflight.remove(&id) else {
            return;
        };
        let error: Option<WorkerError> = (msg.kind() == "error").then(|| {
            msg.parse()
                .unwrap_or_else(|e| WorkerError::new("protocol", e.to_string()))
        });
        match what {
            Inflight::Hello => match error {
                Some(e) => self.worker_failed(Text::Worker(e.text())),
                None => match msg.parse::<Hello>() {
                    Ok(h) if h.protocol == protocol::PROTOCOL => self.on_hello(h),
                    Ok(h) => self.worker_failed(
                        Text::from(Msg::ProtocolMismatch)
                            .arg("version", h.audace_display.as_str())
                            .arg("worker", h.protocol.to_string())
                            .arg("viewer", protocol::PROTOCOL.to_string()),
                    ),
                    Err(e) => self.worker_failed(e.text()),
                },
            },
            Inflight::Open => {
                self.opening = None;
                match error {
                    Some(e) => self.notify(
                        Level::Error,
                        Text::from(Msg::CannotOpen).arg("reason", Text::Worker(e.text())),
                    ),
                    None => match msg.parse::<FileDescription>() {
                        Ok(desc) => self.on_file(desc),
                        Err(e) => self.notify(Level::Error, e.text()),
                    },
                }
            }
            Inflight::Render {
                view,
                key,
                superseded,
                ..
            } => {
                if Some(id) == self.current_render {
                    self.current_render = None;
                    self.progress = None;
                }
                if superseded {
                    return;
                }
                match error {
                    Some(e) if e.kind == "cancelled" => {}
                    Some(e) => {
                        self.errors.insert(view, (key, e));
                    }
                    None => match views::decode(view, msg, &self.ctx) {
                        Ok((result, warnings)) => self.on_result(view, key, result, warnings, now),
                        Err(e) => {
                            self.errors
                                .insert(view, (key, WorkerError::new("protocol", e)));
                        }
                    },
                }
            }
            Inflight::Frame => {
                let Some(player) = self.scope.as_mut() else {
                    return;
                };
                player.in_flight = false;
                if error.is_some() {
                    player.playing = false;
                    return;
                }
                let head = (
                    msg.header.get("session").and_then(Value::as_u64),
                    msg.header.get("local").and_then(Value::as_u64),
                    msg.header.get("line").and_then(Value::as_u64),
                    msg.header.get("t").and_then(Value::as_f64),
                );
                if let (Some(session), Some(local), Some(line), Some(t)) = head
                    && session == player.result.meta.session
                    && let Ok(frame) = views::decode_frame(&mut msg, local, line, t)
                {
                    player.frame = frame;
                }
            }
        }
    }

    fn on_hello(&mut self, hello: Hello) {
        let fresh = self.options.is_empty();
        if fresh {
            self.options = params::options_from_schema(&hello.views);
            self.apply_startup_sets();
        }
        self.hello = Some(hello);
        self.worker_state = WorkerState::Ready;
        // A restarted worker serves the file already open as it is: the
        // results and refusals stand (a computation the user cancelled is
        // not run again). A file asked for meanwhile (the command line, Load
        // while the worker started, an open the old worker never answered)
        // is opened now.
        if let Some(p) = self.startup.file.take()
            && self.file.as_ref().is_none_or(|f| f.path != p)
        {
            self.open_file(p);
        }
    }

    fn apply_startup_sets(&mut self) {
        let sets = self.startup.sets.clone();
        for (dest, text) in sets {
            let view = self.startup.view.unwrap_or(View::Heatmap);
            let Some(opts) = self.options.get_mut(&view) else {
                continue;
            };
            let Some(kind) = opts.schema.option(&dest).map(|o| o.kind) else {
                self.notify(
                    Level::Warning,
                    Text::from(Msg::SetNoOption)
                        .arg("view", view.cli_name())
                        .arg("option", dest.as_str()),
                );
                continue;
            };
            let value = match kind {
                protocol::OptionKind::Flag => {
                    json!(matches!(text.as_str(), "1" | "true" | "yes" | "on"))
                }
                protocol::OptionKind::Int | protocol::OptionKind::Float => {
                    match text.parse::<f64>() {
                        Ok(x) => params::number(kind, x),
                        Err(_) => {
                            self.notify(
                                Level::Warning,
                                Text::from(Msg::SetNotNumber)
                                    .arg("option", dest.as_str())
                                    .arg("value", text.as_str()),
                            );
                            continue;
                        }
                    }
                }
                _ => json!(text),
            };
            opts.set(&dest, value);
        }
    }

    // --- Files -----------------------------------------------------------------

    pub fn open_file(&mut self, path: String) {
        let path = path.trim().to_string();
        if path.is_empty() {
            return;
        }
        let path = std::path::absolute(&path)
            .map(|p| p.display().to_string())
            .unwrap_or(path);
        self.path_input = path.clone();
        if !self.worker_ready() {
            self.startup.file = Some(path);
            return;
        }
        let mut fields = Map::new();
        fields.insert("path".into(), json!(path));
        if self.request("open", fields, Inflight::Open).is_some() {
            self.opening = Some(path);
        }
    }

    fn on_file(&mut self, desc: FileDescription) {
        self.settings.remember_file(&desc.path);
        self.save_settings();
        let same_file = self.file.as_ref().is_some_and(|f| f.path == desc.path);
        if !same_file {
            self.channel = None;
            self.unit = "rad".into();
            self.zoom.clear();
            self.nav.clear();
            self.scope = None;
            self.view = self
                .startup
                .view
                .take()
                .or_else(|| View::from_cli(&desc.default_view))
                .unwrap_or(View::Heatmap);
            if let Some(unit) = self.startup.unit.take() {
                match desc.units.iter().find(|u| u.name == unit) {
                    Some(u) if u.available => self.unit = unit,
                    Some(u) => {
                        let reason = u.reason_text().unwrap_or_default();
                        self.notify(Level::Warning, reason);
                    }
                    None => self.notify(
                        Level::Warning,
                        Text::from(Msg::UnitNotHere).arg("unit", unit.as_str()),
                    ),
                }
            }
            if let Some(z) = self.startup.zoom.take()
                && self.view.zoom_requests_data()
            {
                self.zoom.insert(self.view, z);
            }
        }
        self.results.clear();
        self.errors.clear();
        self.warnings = protocol::warning_texts(&desc.warnings, &desc.warning_codes);
        // The trace needs a position (the CLI requires one): the middle of the
        // fibre, the index the CLI's own fallback takes, unless one is set
        // that lies on this fibre.
        let on_fibre = |p: &Value| {
            p.as_f64()
                .is_some_and(|m| (0.0..desc.summary.length_m).contains(&m))
        };
        if let Some(trace) = self.options.get_mut(&View::Trace)
            && !on_fibre(trace.get("position"))
        {
            let mid = (desc.summary.positions / 2) as f64 * desc.summary.step_m;
            trace.set("position", json!(mid));
        }
        let info_key = self
            .render_spec_for(View::Info, Some(&desc.path), None, "rad")
            .map(|s| s.key);
        if let Some(key) = info_key {
            self.results
                .insert(View::Info, (key, ViewResult::Info(desc.info_text.clone())));
        }
        self.file = Some(desc);
        // The info text came with the description.
        self.force_render = self.view != View::Info;
    }

    pub fn pick_file(&mut self) {
        if self.dialog.is_some() {
            return;
        }
        let (tx, rx) = mpsc::channel();
        let waker = self.waker.clone();
        let start_dir = self.file.as_ref().and_then(|f| {
            std::path::Path::new(&f.path)
                .parent()
                .map(|p| p.to_path_buf())
        });
        let lang = self.lang;
        std::thread::spawn(move || {
            let mut dialog = rfd::FileDialog::new()
                .set_title(lang.t(Msg::DialogTitle))
                .add_filter(
                    lang.t(Msg::DialogRecordings),
                    &["dat", "h5", "hdf5", "tdms", "sgy", "segy"],
                )
                .add_filter(lang.t(Msg::DialogAllFiles), &["*"]);
            if let Some(dir) = start_dir {
                dialog = dialog.set_directory(dir);
            }
            let _ = tx.send(dialog.pick_file());
            waker();
        });
        self.dialog = Some(Mutex::new(rx));
    }

    fn poll_dialog(&mut self) {
        let Some(rx) = &self.dialog else { return };
        let got = rx.lock().expect("dialog lock").try_recv();
        match got {
            Ok(Some(path)) => {
                self.dialog = None;
                self.open_file(path.display().to_string());
            }
            Ok(None) => self.dialog = None,
            Err(mpsc::TryRecvError::Empty) => {}
            Err(mpsc::TryRecvError::Disconnected) => {
                self.dialog = None;
                self.notify(Level::Warning, Msg::DialogFailed);
            }
        }
    }

    pub fn dialog_open(&self) -> bool {
        self.dialog.is_some()
    }

    // --- Rendering -------------------------------------------------------------

    fn render_spec_for(
        &self,
        view: View,
        path: Option<&str>,
        channel: Option<&str>,
        unit: &str,
    ) -> Option<RenderSpec> {
        let path = path?;
        let opts = self.options.get(&view)?;
        // `info` takes no --channel: it always describes the default one.
        let channel = channel.filter(|_| opts.has("channel"));
        let mut fields = Map::new();
        fields.insert("view".into(), json!(view.cli_name()));
        fields.insert("path".into(), json!(path));
        fields.insert("channel".into(), channel.map_or(Value::Null, |c| json!(c)));
        fields.insert("unit".into(), json!(unit));
        let zoom = if view.zoom_requests_data() {
            self.zoom.get(&view)
        } else {
            None
        };
        fields.insert(
            "options".into(),
            Value::Object(opts.request_options(view, zoom)),
        );
        let key = serde_json::to_string(&fields).expect("request serialises");
        Some(RenderSpec { view, key, fields })
    }

    /// The render the current view needs.
    pub fn render_spec(&self) -> Option<RenderSpec> {
        let path = self.file.as_ref().map(|f| f.path.as_str());
        self.render_spec_for(self.view, path, self.channel.as_deref(), &self.unit)
    }

    fn inflight_key(&self) -> Option<&str> {
        self.current_render
            .and_then(|id| match self.inflight.get(&id) {
                Some(Inflight::Render {
                    key,
                    superseded: false,
                    ..
                }) => Some(key.as_str()),
                _ => None,
            })
    }

    /// Whether the shown result is stale (the options changed since).
    pub fn is_stale(&self) -> bool {
        let Some(spec) = self.render_spec() else {
            return false;
        };
        let shown = self.results.get(&self.view).map(|(k, _)| k.as_str());
        let failed = self.errors.get(&self.view).map(|(k, _)| k.as_str());
        shown != Some(spec.key.as_str()) && failed != Some(spec.key.as_str())
    }

    pub fn busy(&self) -> bool {
        self.current_render.is_some() || self.opening.is_some()
    }

    /// Recompute the current view now (the Render button).
    pub fn render_now(&mut self) {
        self.errors.remove(&self.view);
        self.force_render = true;
    }

    fn maybe_render(&mut self, now: Instant) {
        if !self.worker_ready() || self.opening.is_some() {
            return;
        }
        let Some(spec) = self.render_spec() else {
            return;
        };
        if self.inflight_key() == Some(spec.key.as_str()) {
            self.pending = None;
            return;
        }
        let shown = self
            .results
            .get(&self.view)
            .map(|(k, _)| k == &spec.key)
            .unwrap_or(false);
        let failed = self
            .errors
            .get(&self.view)
            .map(|(k, _)| k == &spec.key)
            .unwrap_or(false);
        // Render (force) recomputes even what is shown.
        if !self.force_render && (shown || failed) {
            self.pending = None;
            self.force_render = false;
            return;
        }
        if !self.force_render {
            if !self.settings.auto_render && self.results.contains_key(&self.view) {
                return;
            }
            match &self.pending {
                Some((key, since)) if key == &spec.key => {
                    if now.duration_since(*since) < DEBOUNCE {
                        return;
                    }
                }
                _ => {
                    // A view never shown renders at once; an edit waits for quiet.
                    if self.results.contains_key(&self.view) || self.errors.contains_key(&self.view)
                    {
                        self.pending = Some((spec.key.clone(), now));
                        return;
                    }
                }
            }
        }
        self.force_render = false;
        self.pending = None;
        self.send_render(spec);
    }

    /// Whether a render is waiting for its debounce (the UI keeps ticking).
    pub fn render_pending(&self) -> bool {
        self.pending.is_some()
    }

    fn send_render(&mut self, spec: RenderSpec) {
        self.supersede_current();
        let what = Inflight::Render {
            view: spec.view,
            key: spec.key.clone(),
            superseded: false,
            cancelled_at: None,
        };
        if spec.view == View::Scope {
            self.scope = None;
        }
        if let Some(id) = self.request("render", spec.fields, what) {
            self.current_render = Some(id);
            self.progress = None;
        }
    }

    fn supersede_current(&mut self) {
        let Some(id) = self.current_render.take() else {
            return;
        };
        self.progress = None;
        if let Some(Inflight::Render {
            superseded,
            cancelled_at,
            ..
        }) = self.inflight.get_mut(&id)
        {
            *superseded = true;
            *cancelled_at = Some(Instant::now());
        }
        if let Some(w) = self.worker.as_mut() {
            let _ = w.cancel(id);
        }
    }

    /// The Cancel button.
    pub fn cancel(&mut self) {
        if let Some(id) = self.current_render
            && let Some(Inflight::Render {
                cancelled_at,
                view,
                key,
                ..
            }) = self.inflight.get_mut(&id)
        {
            *cancelled_at = Some(Instant::now());
            // Not asked again until the options change or Render is pressed.
            let mut cancelled = WorkerError::new("cancelled", "Cancelled.");
            cancelled.code = Some("cancelled".into());
            self.errors.insert(*view, (key.clone(), cancelled));
            if let Some(w) = self.worker.as_mut() {
                let _ = w.cancel(id);
            }
        }
    }

    pub fn cancelling(&self) -> bool {
        self.current_render
            .and_then(|id| self.inflight.get(&id))
            .is_some_and(|w| {
                matches!(
                    w,
                    Inflight::Render {
                        cancelled_at: Some(_),
                        ..
                    }
                )
            })
    }

    fn on_result(
        &mut self,
        view: View,
        key: String,
        result: ViewResult,
        warnings: Vec<WorkerText>,
        now: Instant,
    ) {
        self.result_serial += 1;
        self.errors.remove(&view);
        self.warnings = warnings;
        let nav = self.nav.entry(view).or_default();
        if !self.zoom.contains_key(&view) {
            nav.fit = true;
        }
        if let ViewResult::Scope(s) = &result {
            self.scope = Some(ScopePlayer::new(s.clone(), s.first.clone(), now));
        }
        self.results.insert(view, (key, result));
    }

    /// Clears the zoom of the current view (back to its options' window).
    pub fn reset_zoom(&mut self) {
        self.zoom.remove(&self.view);
        self.nav.entry(self.view).or_default().fit = true;
    }

    // --- Oscilloscope ---------------------------------------------------------

    fn tick_scope(&mut self, now: Instant) {
        if self.view != View::Scope {
            if let Some(p) = self.scope.as_mut() {
                p.set_playing(false, now);
            }
            return;
        }
        let Some(player) = self.scope.as_ref() else {
            return;
        };
        if player.in_flight {
            return;
        }
        let local = if player.playing {
            let period = Duration::from_secs_f64(1.0 / player.fps.clamp(1.0, 240.0));
            if player
                .last_request
                .is_some_and(|t| now.duration_since(t) < period)
            {
                return;
            }
            player.local_now(now)
        } else {
            if player.frame.local == player.paused_local {
                return;
            }
            player.paused_local
        };
        let session = player.result.meta.session;
        let mut fields = Map::new();
        fields.insert("session".into(), json!(session));
        fields.insert("local".into(), json!(local));
        if self
            .request("scope_frame", fields, Inflight::Frame)
            .is_some()
            && let Some(p) = self.scope.as_mut()
        {
            p.in_flight = true;
            p.last_request = Some(now);
        }
    }

    /// Whether something time-driven is under way (the UI keeps repainting).
    pub fn animating(&self) -> bool {
        self.busy()
            || self.render_pending()
            || matches!(self.worker_state, WorkerState::Starting { .. })
            || (self.view == View::Scope && self.scope.as_ref().is_some_and(|p| p.playing))
            || self.startup.screenshot.is_some()
    }

    /// For `--screenshot`: the requested view is drawn and nothing is
    /// pending, or the worker could not run (its error is what is shown).
    pub fn screenshot_ready(&self) -> bool {
        if matches!(self.worker_state, WorkerState::Failed(_)) {
            return true;
        }
        self.file.is_some()
            && !self.busy()
            && !self.render_pending()
            && (self.results.contains_key(&self.view) || self.errors.contains_key(&self.view))
            && !self.is_stale()
    }
}

/// What to do when the worker cannot start.
pub fn install_hint(interpreter: &str) -> Text {
    Text::from(Msg::InstallHint).arg("interpreter", interpreter)
}

/// The shared waker type, for main.
pub fn waker(f: impl Fn() + Send + Sync + 'static) -> Waker {
    Arc::new(f)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Result<StartupArgs, String> {
        StartupArgs::parse(args.iter().map(|s| s.to_string()))
    }

    #[test]
    fn parses_the_command_line() {
        let a = parse(&[
            "/data/x.h5",
            "--view",
            "fft",
            "--set",
            "db=true",
            "--set",
            "window = blackman",
        ])
        .unwrap();
        assert_eq!(a.file.as_deref(), Some("/data/x.h5"));
        assert_eq!(a.view, Some(View::Fft));
        assert_eq!(
            a.sets,
            vec![
                ("db".into(), "true".into()),
                ("window".into(), "blackman".into())
            ]
        );
        assert!(parse(&["--view", "demod"]).is_err());
        assert!(parse(&["--set", "nokey"]).is_err());
        assert!(parse(&["a.dat", "b.dat"]).is_err());
        assert!(parse(&["--bogus"]).is_err());
        let z = parse(&["--zoom", "1:2.5:100:300", "--unit", "strain_rate"]).unwrap();
        assert_eq!(
            z.zoom,
            Some(Window {
                t0: 1.0,
                t1: 2.5,
                d0: Some(100.0),
                d1: Some(300.0)
            })
        );
        assert_eq!(z.unit.as_deref(), Some("strain_rate"));
        assert!(parse(&["--zoom", "2:1"]).is_err());
        assert!(parse(&["--zoom", "1:2:3"]).is_err());
        let l = parse(&["--lang", "fr", "--settings"]).unwrap();
        assert_eq!((l.lang, l.settings), (Some(Lang::Fr), true));
        assert_eq!(parse(&[]).unwrap().lang, None);
        assert!(parse(&["--lang", "de"]).is_err());
    }

    #[test]
    fn a_language_chosen_in_settings_applies_at_once_and_is_saved() {
        let startup = parse(&["--lang", "en"]).unwrap();
        let mut app = GuiApp::new(Settings::default(), None, startup, waker(|| {}));
        let dir = std::env::temp_dir().join(format!("audace-gui-lang-{}", std::process::id()));
        app.settings_path = Some(dir.join("settings.json"));
        assert_eq!(app.lang, Lang::En, "--lang wins over the system's locale");
        app.notify(Level::Info, Msg::CancelStuck);
        app.set_language(Some(Lang::Fr));
        assert_eq!(app.lang, Lang::Fr);
        // What is already on screen follows the new language.
        assert!(
            app.notices[0]
                .text
                .render(app.lang)
                .starts_with("Un calcul annulé")
        );
        let (saved, _) = Settings::load_from(app.settings_path.as_deref());
        assert_eq!(saved.language, Some(Lang::Fr));
        app.set_language(None);
        assert_eq!(app.lang, app.system_lang);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn the_install_hint_is_the_same_text_in_english() {
        assert_eq!(
            install_hint("py -3").render(Lang::En),
            "The viewer needs audace-display (with its GUI worker) and invisensing installed in \
             the Python it runs (`py -3`):\n    py -3 -m pip install audace-display\nor, from a \
             checkout of audace-display:\n    py -3 -m pip install -e <path to audace-display>\n\
             The interpreter can be changed in Settings."
        );
    }

    fn player(n_window: u64, trig: f64) -> (ScopePlayer, Instant) {
        let meta: protocol::ScopeMeta = serde_json::from_value(json!({
            "session": 1, "n_window": n_window, "trig_freq": trig, "speed": 1.0, "fps": 30,
            "d_step": 1.0, "length_m": 10.0, "y_label": "y", "title": "t", "ylim": [0.0, 1.0],
            "max_points": 4000, "line_offset": 0, "frame": {"session": 1, "local": 0, "line": 0, "t": 0.0}}))
        .unwrap();
        let frame = || ScopeFrame {
            local: 0,
            line: 0,
            t: 0.0,
            x: vec![],
            y: vec![],
        };
        let now = Instant::now();
        (
            ScopePlayer::new(
                ScopeResult {
                    meta,
                    first: frame(),
                },
                frame(),
                now,
            ),
            now,
        )
    }

    #[test]
    fn the_scope_clock_is_the_pulse_rate_times_speed_looping() {
        let (mut p, t0) = player(500, 1000.0);
        assert_eq!(p.local_now(t0 + Duration::from_millis(100)), 100);
        assert_eq!(
            p.local_now(t0 + Duration::from_millis(600)),
            100,
            "loops over the window"
        );
        p.set_speed(0.5, t0 + Duration::from_millis(100));
        assert_eq!(p.local_now(t0 + Duration::from_millis(300)), 200);
    }

    #[test]
    fn pausing_freezes_the_line_and_resumes_from_it() {
        let (mut p, t0) = player(1000, 1000.0);
        p.set_playing(false, t0 + Duration::from_millis(250));
        assert_eq!(p.paused_local, 250);
        p.set_playing(true, t0 + Duration::from_secs(10));
        assert_eq!(
            p.local_now(t0 + Duration::from_secs(10) + Duration::from_millis(5)),
            255
        );
    }
}
