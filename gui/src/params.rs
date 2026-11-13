//! The views and their options. The options, their kinds and their defaults
//! come from the CLI's own parser (the worker's `hello`); this module only
//! groups them for the panel and turns the values into a request.

use std::collections::BTreeMap;

use serde_json::{Map, Value, json};

use crate::protocol::{OptionKind, OptionSchema, ViewSchema};

/// A view of the viewer; each is a CLI subcommand.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum View {
    Info,
    Heatmap,
    Bands,
    Fft,
    Trace,
    Scope,
    Inspect,
}

impl View {
    pub const ALL: [View; 7] = [
        View::Info,
        View::Heatmap,
        View::Bands,
        View::Fft,
        View::Trace,
        View::Scope,
        View::Inspect,
    ];

    /// The CLI subcommand.
    pub fn cli_name(self) -> &'static str {
        match self {
            View::Info => "info",
            View::Heatmap => "heatmap",
            View::Bands => "bandheatmaps",
            View::Fft => "fft",
            View::Trace => "trace",
            View::Scope => "scope",
            View::Inspect => "inspect",
        }
    }

    pub fn from_cli(name: &str) -> Option<View> {
        View::ALL.into_iter().find(|v| v.cli_name() == name)
    }

    pub fn label(self) -> &'static str {
        match self {
            View::Info => "Info",
            View::Heatmap => "Waterfall",
            View::Bands => "Band heatmaps",
            View::Fft => "Spectrum (FFT)",
            View::Trace => "Time trace",
            View::Scope => "Oscilloscope",
            View::Inspect => "Inspect location",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            View::Info => "Header metadata and statistics of the default channel.",
            View::Heatmap => "Distance x time waterfall, decimated to screen resolution.",
            View::Bands => "One waterfall per temporal frequency band, plus the global one.",
            View::Fft => "Temporal spectrum (FFT along the pulses) at one or more positions.",
            View::Trace => "Time series at one position, min/max envelope.",
            View::Scope => "Replays the lines (pulses) in real time, looping.",
            View::Inspect => "One location: waveform and its spectrum.",
        }
    }

    /// Views whose plot can be zoomed into a new window computed by the worker.
    pub fn zoom_requests_data(self) -> bool {
        matches!(self, View::Heatmap | View::Bands | View::Trace)
    }
}

/// How the options of a view are grouped in the panel: `(title, dests)`.
/// An option of the CLI that no group names lands in "Other options", so an
/// option the CLI gains is still offered.
pub fn layout(view: View) -> &'static [(&'static str, &'static [&'static str])] {
    const WINDOW: &[&str] = &[
        "start_time",
        "duration",
        "start_distance",
        "end_distance",
        "max_pulses",
    ];
    match view {
        View::Info => &[],
        View::Heatmap => &[
            ("Time and distance window", WINDOW),
            ("Decimation", &["max_time_bins", "max_space_bins", "reduce"]),
            ("Colour", &["variance", "db", "vmin", "vmax", "cmap"]),
        ],
        View::Bands => &[
            (
                "Bands (Hz)",
                &["f0", "f1", "f2", "f3", "no_detrend", "variance"],
            ),
            ("Time and distance window", WINDOW),
            ("Resolution", &["max_time_bins", "max_space_bins"]),
            ("Colour", &["cmap"]),
        ],
        View::Fft => &[
            ("Positions", &["position", "position_range"]),
            ("Spectrum", &["window", "no_detrend", "fmax", "db"]),
            (
                "Time window",
                &["start_time", "duration", "subsample_time", "max_pulses"],
            ),
        ],
        View::Trace => &[
            ("Position", &["position"]),
            (
                "Time window",
                &["start_time", "duration", "subsample_time", "max_pulses"],
            ),
            ("Display", &["max_points"]),
        ],
        View::Scope => &[
            ("Loop", &["start_time", "duration"]),
            ("Display", &["max_points"]),
        ],
        View::Inspect => &[
            ("Location", &["index", "position"]),
            (
                "Time window",
                &["start_time", "duration", "subsample_time", "max_pulses"],
            ),
            (
                "Waveform and spectrum",
                &["no_detrend", "clip_percentile", "window", "fmax", "fft_log"],
            ),
        ],
    }
}

/// Options the panel shows elsewhere: the channel and the unit have their
/// own controls; the oscilloscope's playback is driven live by the viewer.
pub fn handled_elsewhere(view: View, dest: &str) -> bool {
    dest == "channel"
        || UNIT_DESTS.contains(&dest)
        || (view == View::Scope && matches!(dest, "speed" | "fps" | "line"))
}

/// Options the CLI accepts for a view but the view does not use (`fft` and
/// `trace` take `--start-distance` / `--end-distance` and ignore them).
pub fn ignored(view: View, dest: &str) -> bool {
    matches!(view, View::Fft | View::Trace) && matches!(dest, "start_distance" | "end_distance")
}

/// The CLI options that choose the unit of a phase.
pub const UNIT_DESTS: &[&str] = &["nanostrain", "strain_rate"];

/// A time and distance window chosen by zooming a plot.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Window {
    pub t0: f64,
    pub t1: f64,
    pub d0: Option<f64>,
    pub d1: Option<f64>,
}

/// The values of a view's options, keyed by the CLI's `dest`.
#[derive(Debug, Clone)]
pub struct ViewOptions {
    pub schema: ViewSchema,
    values: BTreeMap<String, Value>,
}

impl ViewOptions {
    /// Every option at the CLI's default.
    pub fn new(schema: ViewSchema) -> ViewOptions {
        let mut o = ViewOptions {
            schema,
            values: BTreeMap::new(),
        };
        o.reset();
        o
    }

    pub fn reset(&mut self) {
        self.values = self
            .schema
            .options
            .iter()
            .map(|o| (o.dest.clone(), o.default.clone()))
            .collect();
    }

    pub fn get(&self, dest: &str) -> &Value {
        self.values.get(dest).unwrap_or(&Value::Null)
    }

    pub fn set(&mut self, dest: &str, value: Value) {
        if self.schema.option(dest).is_some() {
            self.values.insert(dest.to_string(), value);
        }
    }

    pub fn has(&self, dest: &str) -> bool {
        self.schema.option(dest).is_some()
    }

    pub fn is_default(&self, dest: &str) -> bool {
        self.schema
            .option(dest)
            .is_none_or(|o| &o.default == self.get(dest))
    }

    /// The options in the panel's groups, then the ones no group names.
    pub fn grouped(&self, view: View) -> Vec<(&'static str, Vec<&OptionSchema>)> {
        let mut out = Vec::new();
        let mut placed = std::collections::BTreeSet::new();
        for (title, dests) in layout(view) {
            let opts: Vec<&OptionSchema> =
                dests.iter().filter_map(|d| self.schema.option(d)).collect();
            placed.extend(dests.iter().copied());
            if !opts.is_empty() {
                out.push((*title, opts));
            }
        }
        let other: Vec<&OptionSchema> = self
            .schema
            .options
            .iter()
            .filter(|o| {
                !placed.contains(o.dest.as_str())
                    && !handled_elsewhere(view, &o.dest)
                    && !ignored(view, &o.dest)
            })
            .collect();
        if !other.is_empty() {
            out.push(("Other options", other));
        }
        out
    }

    /// The `options` object of a render request. `zoom` replaces the time
    /// and distance window. In a mutually exclusive group only the first set
    /// option is sent.
    pub fn request_options(&self, view: View, zoom: Option<&Window>) -> Map<String, Value> {
        let mut out = Map::new();
        for o in &self.schema.options {
            if handled_elsewhere(view, &o.dest) || ignored(view, &o.dest) {
                continue;
            }
            let v = self.get(&o.dest);
            if v.is_null() || (o.kind == OptionKind::Flag && v == &Value::Bool(false)) {
                continue;
            }
            out.insert(o.dest.clone(), v.clone());
        }
        for group in &self.schema.exclusive {
            let mut seen = false;
            for dest in group {
                if out.contains_key(dest) {
                    if seen {
                        out.remove(dest);
                    }
                    seen = true;
                }
            }
        }
        if let Some(z) = zoom {
            out.insert("start_time".into(), json!(z.t0.max(0.0)));
            out.insert("duration".into(), json!((z.t1 - z.t0.max(0.0)).max(0.0)));
            if self.has("start_distance") && !ignored(view, "start_distance") {
                match (z.d0, z.d1) {
                    (Some(d0), Some(d1)) => {
                        out.insert("start_distance".into(), json!(d0.max(0.0)));
                        out.insert("end_distance".into(), json!(d1));
                    }
                    _ => {
                        out.remove("start_distance");
                        out.remove("end_distance");
                    }
                }
            }
            // The zoomed window is the whole read: a pulse cap would cut it.
            out.remove("max_pulses");
        }
        out
    }
}

/// Builds every view's options from the worker's schema.
pub fn options_from_schema(views: &BTreeMap<String, ViewSchema>) -> BTreeMap<View, ViewOptions> {
    views
        .iter()
        .filter_map(|(name, schema)| {
            View::from_cli(name).map(|v| (v, ViewOptions::new(schema.clone())))
        })
        .collect()
}

/// A JSON number for the option kind: an integer for `int`.
pub fn number(kind: OptionKind, x: f64) -> Value {
    match kind {
        OptionKind::Int => json!(x.round() as i64),
        _ => json!(x),
    }
}

/// A short human name for a `dest`: `max_time_bins` -> `Max time bins`.
pub fn humanize(dest: &str) -> String {
    let words = dest.replace('_', " ");
    let mut c = words.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    fn schema(v: Value) -> ViewSchema {
        ViewSchema::deserialize(&v).unwrap()
    }

    fn opt(dest: &str, kind: &str, default: Value) -> Value {
        json!({"dest": dest, "flag": format!("--{}", dest.replace('_', "-")), "kind": kind,
               "default": default, "choices": null, "required": false, "help": ""})
    }

    fn heatmap() -> ViewOptions {
        ViewOptions::new(schema(json!({"options": [
            opt("channel", "str", Value::Null),
            opt("start_time", "float", Value::Null),
            opt("duration", "float", Value::Null),
            opt("start_distance", "float", Value::Null),
            opt("end_distance", "float", Value::Null),
            opt("max_time_bins", "int", json!(2000)),
            opt("max_pulses", "int", Value::Null),
            opt("db", "flag", json!(false)),
            opt("nanostrain", "flag", json!(false)),
            opt("strain_rate", "flag", json!(false)),
            opt("brand_new_option", "int", json!(3)),
        ], "exclusive": []})))
    }

    #[test]
    fn values_start_at_the_cli_defaults() {
        let o = heatmap();
        assert_eq!(o.get("max_time_bins"), &json!(2000));
        assert!(o.get("start_time").is_null());
        assert!(o.is_default("db"));
    }

    #[test]
    fn the_request_carries_set_values_only() {
        let mut o = heatmap();
        o.set("db", json!(true));
        o.set("start_time", json!(1.5));
        let r = o.request_options(View::Heatmap, None);
        assert_eq!(r.get("db"), Some(&json!(true)));
        assert_eq!(r.get("start_time"), Some(&json!(1.5)));
        assert_eq!(r.get("max_time_bins"), Some(&json!(2000)));
        assert!(
            !r.contains_key("duration"),
            "null options take the CLI default"
        );
        assert!(
            !r.contains_key("channel")
                && !r.contains_key("nanostrain")
                && !r.contains_key("strain_rate")
        );
    }

    #[test]
    fn a_zoom_replaces_the_window_and_drops_the_pulse_cap() {
        let mut o = heatmap();
        o.set("max_pulses", json!(1000));
        o.set("start_time", json!(9.0));
        let z = Window {
            t0: -0.5,
            t1: 2.0,
            d0: Some(10.0),
            d1: Some(20.0),
        };
        let r = o.request_options(View::Heatmap, Some(&z));
        assert_eq!(
            r.get("start_time"),
            Some(&json!(0.0)),
            "clamped at the start of the file"
        );
        assert_eq!(r.get("duration"), Some(&json!(2.0)));
        assert_eq!(r.get("start_distance"), Some(&json!(10.0)));
        assert_eq!(r.get("end_distance"), Some(&json!(20.0)));
        assert!(!r.contains_key("max_pulses"));
    }

    #[test]
    fn exclusive_options_send_only_one() {
        let mut o = ViewOptions::new(schema(json!({"options": [
            opt("position", "str", Value::Null), opt("position_range", "str", Value::Null)],
            "exclusive": [["position", "position_range"]]})));
        o.set("position", json!("10,20"));
        o.set("position_range", json!("5:50"));
        let r = o.request_options(View::Fft, None);
        assert!(r.contains_key("position") && !r.contains_key("position_range"));
    }

    #[test]
    fn an_unknown_cli_option_lands_in_other_options() {
        let o = heatmap();
        let groups = o.grouped(View::Heatmap);
        let (title, opts) = groups.last().unwrap();
        assert_eq!(*title, "Other options");
        assert_eq!(
            opts.iter().map(|o| o.dest.as_str()).collect::<Vec<_>>(),
            vec!["brand_new_option"]
        );
    }

    #[test]
    fn the_distance_window_is_not_offered_where_the_cli_ignores_it() {
        let o = heatmap();
        let shown: Vec<String> = o
            .grouped(View::Trace)
            .into_iter()
            .flat_map(|(_, v)| v.into_iter().map(|o| o.dest.clone()))
            .collect();
        assert!(!shown.contains(&"start_distance".to_string()));
        let r = o.request_options(
            View::Trace,
            Some(&Window {
                t0: 0.0,
                t1: 1.0,
                d0: None,
                d1: None,
            }),
        );
        assert!(!r.contains_key("start_distance"));
    }

    #[test]
    fn views_round_trip_their_cli_names() {
        for v in View::ALL {
            assert_eq!(View::from_cli(v.cli_name()), Some(v));
        }
        assert_eq!(View::from_cli("demod"), None);
    }

    #[test]
    fn integers_stay_integers() {
        assert_eq!(number(OptionKind::Int, 1999.6), json!(2000));
        assert_eq!(number(OptionKind::Float, 0.25), json!(0.25));
        assert_eq!(humanize("max_time_bins"), "Max time bins");
    }
}
