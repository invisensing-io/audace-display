//! The CLI's fixed texts in the viewer's language: the quantities of the
//! axis and colour-bar labels, the plot titles, the inspection's statistics,
//! the `info` text, the options' names and help.
//!
//! The worker hands them over as the CLI wrote them, in English. English is
//! shown as it is. In French each text is rebuilt from the pattern the CLI
//! writes it with, its numbers the French way; a text that does not match its
//! pattern, or a word the catalogue does not know (a channel, a mode, a file
//! name, a colormap), is shown as the CLI wrote it.

use crate::i18n::{Lang, Msg, NBSP, localize_number};
use crate::params::View;

fn fr(lang: Lang) -> bool {
    lang == Lang::Fr
}

/// The French words of an English text the catalogue holds word for word
/// among `msgs`, or the text itself.
fn word(lang: Lang, text: &str, msgs: &[Msg]) -> String {
    msgs.iter()
        .find(|m| m.text(Lang::En) == text)
        .map(|m| m.text(lang).to_string())
        .unwrap_or_else(|| text.to_string())
}

const HEADS: &[Msg] = &[
    Msg::QPhase,
    Msg::QStrain,
    Msg::QStrainRate,
    Msg::QAmplitude,
    Msg::QAmplitudeCap,
    Msg::QScriptOutput,
    Msg::QVariance,
];

const UNITS: &[Msg] = &[Msg::QSourceUnit, Msg::QFrontEndCount];

/// The quantity of a label (`strain rate`, `phase`...) in `lang`; a notation
/// (`ADC`, `I`, `arg(I+jQ)`, `|signal|`) is unchanged.
pub fn quantity_head(lang: Lang, head: &str) -> String {
    word(lang, head, HEADS)
}

/// The unit of a label in `lang`: a unit that is a word (`source unit²`) is
/// translated, a symbol (`nm/m`, `rad`, `V`) is not.
fn unit(lang: Lang, unit: &str) -> String {
    word(lang, unit, UNITS)
}

/// A label of an axis or a colour bar: `strain rate (nm/m/s)`,
/// `log10 Var[strain] (nm/m)^2`, `RMS[phase] (rad)`, `phase (dB, ref=1.51
/// rad)`, `Amplitude (dB, ref=0.00499)`, `Amplitude (log)`.
pub fn quantity(lang: Lang, label: &str) -> String {
    if !fr(lang) {
        return label.to_string();
    }
    for prefix in ["log10 Var[", "Var[", "RMS["] {
        if let Some((head, tail)) = label.strip_prefix(prefix).and_then(|r| r.split_once(']')) {
            let tail = UNITS.iter().fold(tail.to_string(), |t, m| {
                t.replace(m.text(Lang::En), m.text(lang))
            });
            return format!("{prefix}{}]{tail}", quantity_head(lang, head));
        }
    }
    if let Some((head, inner)) = label
        .split_once(" (dB, ref=")
        .and_then(|(h, r)| Some((h, r.strip_suffix(')')?)))
    {
        let (reference, unit) = match inner.split_once(' ') {
            Some((r, u)) => (r, format!(" {u}")),
            None => (inner, String::new()),
        };
        let reference = format!("{}{unit}", localize_number(lang, reference));
        return format!(
            "{} ({})",
            quantity_head(lang, head),
            lang.f(Msg::DbRef, &[("reference", &reference)])
        );
    }
    if let Some((head, u)) = label.strip_suffix(')').and_then(|l| l.rsplit_once(" (")) {
        return format!("{} ({})", quantity_head(lang, head), unit(lang, u));
    }
    quantity_head(lang, label)
}

/// The quantity of a unit the worker offers (`rad`, `nanostrain`,
/// `strain_rate`), with its unit.
pub fn unit_choice(lang: Lang, name: &str, quantity: &str, unit: Option<&str>) -> String {
    if name == "rad" {
        return lang.t(Msg::UnitAsStored).to_string();
    }
    let q = quantity_head(lang, quantity);
    match unit {
        Some(u) => format!("{q} ({u})"),
        None => q,
    }
}

/// `channel=phase` -> `canal=phase`; any other source (`demod=...`) as is.
fn source(lang: Lang, s: &str) -> String {
    match s.strip_prefix("channel=") {
        Some(c) => lang.f(Msg::SourceChannel, &[("channel", c)]),
        None => s.to_string(),
    }
}

/// A plot title of `view`, as the CLI builds it.
pub fn title(lang: Lang, view: View, title: &str) -> String {
    if !fr(lang) {
        return title.to_string();
    }
    let n = |s: &str| localize_number(lang, s);
    let built = match view {
        View::Heatmap => title.rsplit_once(" -- mode=").and_then(|(name, rest)| {
            let (mode, channel) = rest.split_once(", channel=")?;
            Some(lang.f(
                Msg::TitleHeatmap,
                &[("name", name), ("mode", mode), ("channel", channel)],
            ))
        }),
        View::Bands => title
            .strip_suffix(')')
            .and_then(|t| t.rsplit_once(" -- band heatmaps (mode="))
            .and_then(|(name, rest)| {
                let (mode, channel) = rest.split_once(", channel=")?;
                Some(lang.f(
                    Msg::TitleBands,
                    &[("name", name), ("mode", mode), ("channel", channel)],
                ))
            }),
        View::Fft => title
            .strip_prefix("Temporal FFT -- ")
            .and_then(|t| t.split_once('\n'))
            .and_then(|(name, line)| {
                let (mode, rest) = line.strip_prefix("mode=")?.split_once(", channel=")?;
                let (channel, rest) = rest.split_once(", ")?;
                let (positions, window) = rest.rsplit_once(", window=")?;
                let positions = fft_positions(lang, positions)?;
                Some(lang.f(
                    Msg::TitleFft,
                    &[
                        ("name", name),
                        ("mode", mode),
                        ("channel", channel),
                        ("positions", &positions),
                        ("window", window),
                    ],
                ))
            }),
        View::Trace => title
            .strip_prefix("Time trace -- ")
            .and_then(|t| t.split_once("\nchannel="))
            .and_then(|(name, rest)| {
                let (channel, position) = rest.split_once(", position=")?;
                let position = position.strip_suffix(" m")?;
                Some(lang.f(
                    Msg::TitleTrace,
                    &[
                        ("name", name),
                        ("channel", channel),
                        ("position", &n(position)),
                    ],
                ))
            }),
        View::Scope => title
            .strip_suffix(" lines/s)")
            .and_then(|t| t.rsplit_once(" (oscilloscope x"))
            .and_then(|(left, rest)| {
                let (name, channel) = left.rsplit_once(" -- ")?;
                let (speed, rate) = rest.split_once(", ")?;
                Some(lang.f(
                    Msg::TitleScope,
                    &[
                        ("name", name),
                        ("channel", channel),
                        ("speed", &n(speed)),
                        ("rate", &n(rate)),
                    ],
                ))
            }),
        View::Inspect => title
            .strip_suffix(" m)")
            .and_then(|t| t.rsplit_once(", location index "))
            .and_then(|(left, rest)| {
                let (name, src) = left.rsplit_once(" -- ")?;
                let (index, position) = rest.split_once(" (")?;
                Some(lang.f(
                    Msg::TitleInspect,
                    &[
                        ("name", name),
                        ("source", &source(lang, src)),
                        ("index", index),
                        ("position", &n(position)),
                    ],
                ))
            }),
        View::Info => None,
    };
    built.unwrap_or_else(|| title.to_string())
}

/// The positions part of an FFT title.
fn fft_positions(lang: Lang, text: &str) -> Option<String> {
    let n = |s: &str| localize_number(lang, s);
    if let Some(p) = text
        .strip_prefix("position=")
        .and_then(|p| p.strip_suffix(" m"))
    {
        return Some(lang.f(Msg::FftAtPosition, &[("position", &n(p))]));
    }
    if let Some(list) = text
        .strip_prefix("positions=")
        .and_then(|p| p.strip_suffix(" m"))
    {
        // A decimal comma would merge the list: semicolons between items.
        let items: Vec<String> = list.split(',').map(n).collect();
        let list = items.join(&format!("{NBSP}; "));
        return Some(lang.f(Msg::FftAtPositions, &[("positions", &list)]));
    }
    if let Some(rest) = text
        .strip_prefix("range ")
        .and_then(|r| r.strip_suffix(" positions, averaged)"))
    {
        let (span, count) = rest.split_once(" m (")?;
        let (from, to) = span.split_once('-')?;
        return Some(lang.f(
            Msg::FftRange,
            &[("from", &n(from)), ("to", &n(to)), ("count", count)],
        ));
    }
    let count = text
        .strip_prefix("whole fiber (")?
        .strip_suffix(" positions, averaged)")?;
    Some(lang.f(Msg::FftWhole, &[("count", count)]))
}

/// The title of one panel of the band heatmaps.
pub fn panel_title(lang: Lang, title: &str) -> String {
    if !fr(lang) {
        return title.to_string();
    }
    let fixed = word(lang, title, &[Msg::PanelGlobalMean, Msg::PanelGlobalWhole]);
    if fixed != title {
        return fixed;
    }
    title
        .strip_suffix(')')
        .and_then(|t| t.split_once(" Hz ("))
        .and_then(|(span, kind)| {
            let (low, high) = span.split_once('-')?;
            let kind = word(
                lang,
                kind,
                &[Msg::KindRms, Msg::KindVariance, Msg::KindLogVariance],
            );
            Some(lang.f(
                Msg::PanelBand,
                &[("low", low), ("high", high), ("kind", &kind)],
            ))
        })
        .unwrap_or_else(|| title.to_string())
}

/// A stage of the worker's progress (`reading`).
pub fn stage(lang: Lang, stage: &str) -> String {
    word(lang, stage, &[Msg::StageReading])
}

/// The statistics beside the inspected waveform: `Key: value` lines.
pub fn stats(lang: Lang, text: &str) -> String {
    if !fr(lang) {
        return text.to_string();
    }
    const KEYS: &[Msg] = &[
        Msg::StatIndex,
        Msg::StatPosition,
        Msg::StatWindow,
        Msg::StatSamples,
        Msg::StatRate,
        Msg::StatMean,
        Msg::StatStd,
        Msg::StatMin,
        Msg::StatMax,
    ];
    text.lines()
        .map(|line| match line.split_once(": ") {
            Some((key, value)) => {
                format!("{}{NBSP}: {}", word(lang, key, KEYS), values(lang, value))
            }
            None => line.to_string(),
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// The numbers of a value the French way: each word, and each side of a
/// `a-b` span, that is a number (`640.00 m`, `0-10 s`, `+1.5e-3`).
fn values(lang: Lang, value: &str) -> String {
    value
        .split(' ')
        .map(|w| {
            if let Some(v) = crate::i18n::french_number(w) {
                return v;
            }
            match w.split_once('-') {
                Some((a, b)) if !a.is_empty() => {
                    match (crate::i18n::french_number(a), crate::i18n::french_number(b)) {
                        (Some(a), Some(b)) => format!("{a}-{b}"),
                        _ => w.to_string(),
                    }
                }
                _ => localize_number(lang, w),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The trigger frequency as the CLI writes it: `75,000 Hz` or `75,000 Hz
/// (actual 75,007.5008 Hz, +100.0 ppm)`.
pub fn trig_frequency(lang: Lang, text: &str) -> String {
    if !fr(lang) {
        return text.to_string();
    }
    let Some((requested, rest)) = text.split_once(" Hz") else {
        return text.to_string();
    };
    let requested = localize_number(lang, requested);
    if rest.is_empty() {
        return format!("{requested} Hz");
    }
    let detail = rest
        .strip_prefix(" (actual ")
        .and_then(|r| r.strip_suffix(')'))
        .and_then(|r| {
            let (actual, ppm) = match r.split_once(" Hz, ") {
                Some((a, p)) => (a, Some(p.strip_suffix(" ppm")?)),
                None => (r.strip_suffix(" Hz")?, None),
            };
            let mut d = lang.f(
                Msg::TrigActual,
                &[("frequency", &localize_number(lang, actual))],
            );
            if let Some(p) = ppm {
                d.push_str(&format!(", {} ppm", localize_number(lang, p)));
            }
            Some(d)
        });
    match detail {
        Some(d) => format!("{requested} Hz ({d})"),
        None => text.to_string(),
    }
}

/// How the file's times were stamped: `none: count (no time map)`,
/// `cardclock-v1: corrected, +0.098 ms vs the count (tracking, +/-61 us)`.
pub fn time_correction(lang: Lang, text: &str) -> String {
    if !fr(lang) {
        return text.to_string();
    }
    if let Some(tag) = text.strip_suffix(": count (no time map)") {
        return lang.f(Msg::CorrectionCount, &[("tag", tag)]);
    }
    let Some((tag, rest)) = text.split_once(": corrected") else {
        return text.to_string();
    };
    let mut out = lang.f(Msg::CorrectionCorrected, &[("tag", tag)]);
    let (parts, detail) = match rest.split_once(" (") {
        Some((p, d)) => (p, d.strip_suffix(')').map(|d| values(lang, d))),
        None => (rest, None),
    };
    for part in parts.split(", ").filter(|p| !p.is_empty()) {
        out.push_str(", ");
        if let Some(delta) = part.strip_suffix(" ms vs the count") {
            out.push_str(&lang.f(
                Msg::CorrectionVsCount,
                &[("delta", &localize_number(lang, delta))],
            ));
        } else if part == Msg::CorrectionStartTime.text(Lang::En) {
            out.push_str(lang.t(Msg::CorrectionStartTime));
        } else {
            return text.to_string();
        }
    }
    if let Some(d) = detail {
        out.push_str(&format!(" ({d})"));
    }
    out
}

/// The compression of a file: `ZSTD_LOSSY_1E4 (phase on a 1.221e-04 rad
/// grid = 0.0135 nm/m, error <= 6.104e-05 rad)`.
pub fn compression(lang: Lang, text: &str) -> String {
    if !fr(lang) {
        return text.to_string();
    }
    let Some((name, detail)) = text.strip_suffix(')').and_then(|t| t.split_once(" (")) else {
        return text.to_string();
    };
    let strain = |s: &str| -> Option<String> {
        if s.is_empty() {
            return Some(String::new());
        }
        let (v, u) = s.strip_prefix(" = ")?.split_once(' ')?;
        Some(format!(" = {} {u}", localize_number(lang, v)))
    };
    let mut items = Vec::new();
    for item in detail.split(", ") {
        let translated = if let Some(rest) = item.strip_prefix("phase on a ") {
            rest.split_once(" rad grid").and_then(|(step, s)| {
                Some(lang.f(
                    Msg::CompressionGrid,
                    &[
                        ("step", &localize_number(lang, step)),
                        ("strain", &strain(s)?),
                    ],
                ))
            })
        } else if let Some(rest) = item.strip_prefix("error <= ") {
            rest.split_once(" rad").and_then(|(error, s)| {
                Some(lang.f(
                    Msg::CompressionError,
                    &[
                        ("error", &localize_number(lang, error)),
                        ("strain", &strain(s)?),
                    ],
                ))
            })
        } else {
            None
        };
        match translated {
            Some(t) => items.push(t),
            None => return text.to_string(),
        }
    }
    format!("{name} ({})", items.join(", "))
}

/// The start time of a file: a date stays as the CLI writes it, `(none)`
/// is translated.
pub fn timestamp(lang: Lang, text: &str) -> String {
    word(lang, text, &[Msg::InfoNone])
}

/// The keys of the `info` text.
const INFO_KEYS: &[Msg] = &[
    Msg::KMode,
    Msg::KContentType,
    Msg::KQuantity,
    Msg::KSourceColumn,
    Msg::KFormatVersion,
    Msg::KCard,
    Msg::KOtherHalf,
    Msg::KGaugeLength,
    Msg::KStrain,
    Msg::KStrainRate,
    Msg::KTimeGaps,
    Msg::KTimestamp,
    Msg::KLastPulse,
    Msg::KTime,
    Msg::KPulses,
    Msg::KDuration,
    Msg::KSampleRate,
    Msg::KTrigFrequency,
    Msg::KLineSize,
    Msg::KPositions,
    Msg::KSpatialStep,
    Msg::KLinePeriod,
    Msg::KFiberLength,
    Msg::KTriggerDelay,
    Msg::KAdcRange,
    Msg::KSampleSize,
    Msg::KSockets,
    Msg::KFlags,
    Msg::KCompression,
];

/// The value of one `info` line in `lang`, by its key.
fn info_value(lang: Lang, key: Msg, value: &str) -> String {
    let n = |s: &str| localize_number(lang, s);
    match key {
        Msg::KQuantity => quantity(lang, value),
        Msg::KGaugeLength
        | Msg::KSampleRate
        | Msg::KSpatialStep
        | Msg::KLinePeriod
        | Msg::KFiberLength
        | Msg::KTriggerDelay
        | Msg::KAdcRange
        | Msg::KPulses
        | Msg::KSockets => values(lang, value),
        Msg::KStrain | Msg::KStrainRate => {
            let (formula, tail) = match value.split_once("; ") {
                Some((f, t)) => (f, Some(t)),
                None => (value, None),
            };
            let mut out = values(lang, formula).replace(" x ", " × ");
            if let Some(t) = tail {
                out.push_str(&format!(
                    "{NBSP}; {}",
                    word(lang, t, &[Msg::InfoLineZeroNone])
                ));
            }
            out
        }
        Msg::KTimeGaps => value
            .split_once(" line(s) after lost shots, NaN: ")
            .map(|(c, lines)| {
                lang.f(
                    Msg::InfoTimeGapsValue,
                    &[("count", &n(c)), ("lines", lines)],
                )
            })
            .unwrap_or_else(|| value.to_string()),
        Msg::KTimestamp | Msg::KLastPulse => timestamp(lang, value),
        Msg::KTime => time_correction(lang, value),
        Msg::KDuration => match value.split_once(" s (file: ") {
            Some((d, file)) => match file.strip_suffix(" s)") {
                Some(f) => format!(
                    "{} s {}",
                    n(d),
                    lang.f(Msg::InfoFileDuration, &[("duration", &n(f))])
                ),
                None => value.to_string(),
            },
            None => values(lang, value),
        },
        Msg::KTrigFrequency => trig_frequency(lang, value),
        Msg::KLineSize => value
            .strip_suffix(" samples/pulse")
            .map(|c| lang.f(Msg::InfoSamplesPerPulse, &[("count", &n(c))]))
            .unwrap_or_else(|| value.to_string()),
        Msg::KPositions => value
            .strip_suffix(" per pulse")
            .map(|c| lang.f(Msg::InfoPerPulse, &[("count", &n(c))]))
            .unwrap_or_else(|| value.to_string()),
        Msg::KCompression => compression(lang, value),
        _ => value.to_string(),
    }
}

/// The header of the statistics: `Stats for channel 'phase' -- strain
/// (nm/m) over 1,000 pulses (2 NaN/inf samples left out):`, or its `no
/// finite sample` form.
fn info_stats(lang: Lang, line: &str) -> Option<String> {
    let rest = line.strip_prefix("Stats for channel '")?;
    let (channel, rest) = rest.split_once('\'')?;
    let (shown, rest) = rest.split_once(" over ")?;
    let (count, rest) = rest.split_once(" pulses")?;
    let shown = match shown.strip_prefix(" -- ") {
        Some(label) => format!(" — {}", quantity(lang, label)),
        None if shown.is_empty() => String::new(),
        None => return None,
    };
    let note = |s: &str| -> Option<String> {
        if s.is_empty() {
            return Some(String::new());
        }
        let c = s
            .strip_prefix(" (")?
            .strip_suffix(" NaN/inf samples left out)")?;
        Some(lang.f(Msg::InfoLeftOut, &[("count", &localize_number(lang, c))]))
    };
    let count = localize_number(lang, count);
    let args = |note: &str| {
        [
            ("channel", channel.to_string()),
            ("shown", shown.clone()),
            ("count", count.clone()),
            ("note", note.to_string()),
        ]
    };
    let render = |msg: Msg, note: &str| {
        let a = args(note);
        let refs: Vec<(&str, &str)> = a.iter().map(|(k, v)| (*k, v.as_str())).collect();
        lang.f(msg, &refs)
    };
    if let Some(n) = rest.strip_suffix(':') {
        return Some(render(Msg::InfoStats, &note(n)?));
    }
    let n = rest.strip_prefix(": no finite sample")?;
    Some(render(Msg::InfoStatsNone, &note(n)?))
}

/// The CLI's `info` text, line by line: the keys translated and aligned
/// again, the values the French way. A line of another shape is kept.
pub fn info(lang: Lang, text: &str) -> String {
    if !fr(lang) {
        return text.to_string();
    }
    enum Line {
        Keyed(String, String),
        Other(String),
    }
    let lines: Vec<Line> = text
        .lines()
        .map(|line| {
            if let Some(path) = line.strip_prefix("File: ") {
                return Line::Other(format!("{}{NBSP}: {path}", lang.t(Msg::KFile)));
            }
            if let Some(body) = line.strip_prefix("  ")
                && !body.starts_with(' ')
                && let Some((key, value)) = body.split_once(": ")
            {
                let key = key.trim_end();
                if let Some(&k) = INFO_KEYS.iter().find(|m| m.text(Lang::En) == key) {
                    return Line::Keyed(k.text(lang).to_string(), info_value(lang, k, value));
                }
            }
            if let Some(stats) = line.strip_prefix("  ").and_then(|l| info_stats(lang, l)) {
                return Line::Other(format!("  {stats}"));
            }
            if let Some(body) = line.strip_prefix("    ")
                && body.contains('=')
            {
                let fields: Vec<String> = body
                    .split("  ")
                    .map(|f| match f.split_once('=') {
                        Some((k, v)) => format!(
                            "{}={}",
                            word(lang, k, &[Msg::InfoMean, Msg::InfoStd]),
                            localize_number(lang, v)
                        ),
                        None => f.to_string(),
                    })
                    .collect();
                return Line::Other(format!("    {}", fields.join("  ")));
            }
            Line::Other(line.to_string())
        })
        .collect();
    let width = lines
        .iter()
        .filter_map(|l| match l {
            Line::Keyed(k, _) => Some(k.chars().count()),
            Line::Other(_) => None,
        })
        .max()
        .unwrap_or(0);
    let mut out = lines
        .into_iter()
        .map(|l| match l {
            Line::Keyed(k, v) => {
                let pad = " ".repeat(width - k.chars().count());
                format!("  {k}{pad}{NBSP}: {v}")
            }
            Line::Other(s) => s,
        })
        .collect::<Vec<_>>()
        .join("\n");
    if text.ends_with('\n') {
        out.push('\n');
    }
    out
}

/// The name of an option: in English the CLI's dest made readable, in
/// French the catalogue's word for a known dest.
pub fn option_name(lang: Lang, dest: &str) -> String {
    let english = crate::params::humanize(dest);
    if !fr(lang) {
        return english;
    }
    const NAMES: &[(&str, Msg)] = &[
        ("channel", Msg::OptChannel),
        ("start_time", Msg::OptStartTime),
        ("duration", Msg::OptDuration),
        ("start_distance", Msg::OptStartDistance),
        ("end_distance", Msg::OptEndDistance),
        ("max_pulses", Msg::OptMaxPulses),
        ("max_time_bins", Msg::OptMaxTimeBins),
        ("max_space_bins", Msg::OptMaxSpaceBins),
        ("reduce", Msg::OptReduce),
        ("variance", Msg::OptVariance),
        ("db", Msg::OptDb),
        ("vmin", Msg::OptVmin),
        ("vmax", Msg::OptVmax),
        ("cmap", Msg::OptCmap),
        ("f0", Msg::OptF0),
        ("f1", Msg::OptF1),
        ("f2", Msg::OptF2),
        ("f3", Msg::OptF3),
        ("no_detrend", Msg::OptNoDetrend),
        ("position", Msg::OptPosition),
        ("position_range", Msg::OptPositionRange),
        ("window", Msg::OptWindow),
        ("fmax", Msg::OptFmax),
        ("subsample_time", Msg::OptSubsampleTime),
        ("max_points", Msg::OptMaxPoints),
        ("index", Msg::OptIndex),
        ("clip_percentile", Msg::OptClipPercentile),
        ("fft_log", Msg::OptFftLog),
        ("speed", Msg::OptSpeed),
        ("fps", Msg::OptFps),
        ("line", Msg::OptLine),
    ];
    NAMES
        .iter()
        .find(|(d, _)| *d == dest)
        .map(|(_, m)| m.text(lang).to_string())
        .unwrap_or(english)
}

/// The CLI's help of an option in `lang`: the catalogue holds the CLI's
/// English help word for word with its translation; a help the CLI words
/// otherwise (a newer CLI) is shown as the CLI wrote it.
pub fn help(lang: Lang, help: &str) -> String {
    const HELPS: &[Msg] = &[
        Msg::HelpChannel,
        Msg::HelpChannelScript,
        Msg::HelpStartTime,
        Msg::HelpLoopStart,
        Msg::HelpDuration,
        Msg::HelpLoopDuration,
        Msg::HelpDurationRead,
        Msg::HelpStartDistance,
        Msg::HelpEndDistance,
        Msg::HelpTimeBinsX,
        Msg::HelpTimeBinsY,
        Msg::HelpSpaceBinsY,
        Msg::HelpSpaceBinsX,
        Msg::HelpReduce,
        Msg::HelpMaxPulses,
        Msg::HelpMaxPulsesShort,
        Msg::HelpDb,
        Msg::HelpDbFft,
        Msg::HelpVmin,
        Msg::HelpVmax,
        Msg::HelpCmap,
        Msg::HelpVariance,
        Msg::HelpF0,
        Msg::HelpF1,
        Msg::HelpF2,
        Msg::HelpF3,
        Msg::HelpNoDetrendBands,
        Msg::HelpNoDetrendFft,
        Msg::HelpNoDetrendInspect,
        Msg::HelpPositionFft,
        Msg::HelpPositionTrace,
        Msg::HelpPositionInspect,
        Msg::HelpPositionRange,
        Msg::HelpWindow,
        Msg::HelpWindowInspect,
        Msg::HelpFmax,
        Msg::HelpFmaxInspect,
        Msg::HelpSubsampleFft,
        Msg::HelpSubsample,
        Msg::HelpMaxPointsTrace,
        Msg::HelpMaxPointsScope,
        Msg::HelpIndex,
        Msg::HelpClipPercentile,
        Msg::HelpFftLog,
    ];
    word(lang, help, HELPS)
}

#[cfg(test)]
mod tests {
    use super::*;

    const FR: Lang = Lang::Fr;

    #[test]
    fn english_is_the_cli_text_unchanged() {
        for s in [
            "strain rate (nm/m/s)",
            "phase.dat -- mode=phase, channel=phase",
            "Mean: 0.5",
        ] {
            assert_eq!(quantity(Lang::En, s), s);
            assert_eq!(title(Lang::En, View::Heatmap, s), s);
            assert_eq!(stats(Lang::En, s), s);
            assert_eq!(info(Lang::En, s), s);
        }
    }

    #[test]
    fn labels_translate_their_quantity_and_keep_their_unit() {
        assert_eq!(
            quantity(FR, "strain rate (nm/m/s)"),
            "vitesse de déformation (nm/m/s)"
        );
        assert_eq!(quantity(FR, "strain (nm/m)"), "déformation (nm/m)");
        assert_eq!(quantity(FR, "phase (rad)"), "phase (rad)");
        assert_eq!(
            quantity(FR, "log10 Var[strain] (nm/m)^2"),
            "log10 Var[déformation] (nm/m)^2"
        );
        assert_eq!(
            quantity(FR, "log10 Var[phase] (rad^2)"),
            "log10 Var[phase] (rad^2)"
        );
        assert_eq!(
            quantity(FR, "Var[strain rate] (nm/m/s)^2"),
            "Var[vitesse de déformation] (nm/m/s)^2"
        );
        assert_eq!(
            quantity(FR, "RMS[strain rate] (nm/m/s)"),
            "RMS[vitesse de déformation] (nm/m/s)"
        );
        assert_eq!(
            quantity(FR, "strain rate (dB, ref=4.02e+03 nm/m/s)"),
            "vitesse de déformation (dB, réf.\u{a0}4,02e+03 nm/m/s)"
        );
        assert_eq!(
            quantity(FR, "Amplitude (dB, ref=0.00499)"),
            "Amplitude (dB, réf.\u{a0}0,00499)"
        );
        assert_eq!(quantity(FR, "Amplitude (nm/m)"), "Amplitude (nm/m)");
        assert_eq!(quantity(FR, "Amplitude (log)"), "Amplitude (log)");
        assert_eq!(quantity(FR, "Amplitude"), "Amplitude");
        assert_eq!(quantity(FR, "arctan(Q/I) (rad)"), "arctan(Q/I) (rad)");
        assert_eq!(quantity(FR, "ADC (V)"), "ADC (V)");
        assert_eq!(quantity(FR, "log10(1 + variance)"), "log10(1 + variance)");
        assert_eq!(
            quantity(FR, "variance (source unit²)"),
            "variance (unité source²)"
        );
        assert_eq!(
            quantity(FR, "amplitude (front-end count)"),
            "amplitude (comptes du front-end)"
        );
        assert_eq!(quantity(FR, "script output"), "sortie du script");
        assert_eq!(
            unit_choice(FR, "strain_rate", "strain rate", Some("nm/m/s")),
            "vitesse de déformation (nm/m/s)"
        );
        assert_eq!(
            unit_choice(FR, "rad", "as stored", None),
            "Telle que stockée"
        );
    }

    #[test]
    fn titles_follow_the_cli_patterns() {
        assert_eq!(
            title(FR, View::Heatmap, "phase.dat -- mode=phase, channel=phase"),
            "phase.dat — mode=phase, canal=phase"
        );
        assert_eq!(
            title(
                FR,
                View::Bands,
                "a -- b.dat -- band heatmaps (mode=phase, channel=phase)"
            ),
            "a -- b.dat — cartes de bandes (mode=phase, canal=phase)"
        );
        assert_eq!(
            title(
                FR,
                View::Fft,
                "Temporal FFT -- phase.dat\nmode=phase, channel=phase, whole fiber (800 positions, averaged), window=hann"
            ),
            "FFT temporelle — phase.dat\nmode=phase, canal=phase, toute la fibre (800 positions, moyennées), fenêtre=hann"
        );
        assert_eq!(
            title(
                FR,
                View::Fft,
                "Temporal FFT -- phase.dat\nmode=phase, channel=phase, positions=9.6,19.2 m, window=hann"
            ),
            "FFT temporelle — phase.dat\nmode=phase, canal=phase, positions=9,6\u{a0}; 19,2 m, fenêtre=hann"
        );
        assert_eq!(
            title(
                FR,
                View::Fft,
                "Temporal FFT -- phase.dat\nmode=phase, channel=phase, range 9.6-30.4 m (14 positions, averaged), window=hann"
            ),
            "FFT temporelle — phase.dat\nmode=phase, canal=phase, plage 9,6-30,4 m (14 positions, moyennées), fenêtre=hann"
        );
        assert_eq!(
            title(
                FR,
                View::Fft,
                "Temporal FFT -- p.dat\nmode=iq, channel=i, position=12.00 m, window=rect"
            ),
            "FFT temporelle — p.dat\nmode=iq, canal=i, position=12,00 m, fenêtre=rect"
        );
        assert_eq!(
            title(
                FR,
                View::Trace,
                "Time trace -- phase.dat\nchannel=phase, position=9.60 m"
            ),
            "Trace temporelle — phase.dat\ncanal=phase, position=9,60 m"
        );
        assert_eq!(
            title(
                FR,
                View::Scope,
                "phase.dat -- phase (oscilloscope x0.25, 75007.5008 lines/s)"
            ),
            "phase.dat — phase (oscilloscope ×0,25, 75007,5008 lignes/s)"
        );
        assert_eq!(
            title(
                FR,
                View::Inspect,
                "phase.dat -- channel=phase, location index 400 (640.00 m)"
            ),
            "phase.dat — canal=phase, emplacement d'index 400 (640,00 m)"
        );
        // A title of another shape is the CLI's.
        assert_eq!(title(FR, View::Heatmap, "My title"), "My title");
        assert_eq!(panel_title(FR, "global (mean)"), "globale (moyenne)");
        assert_eq!(
            panel_title(FR, "global (whole band)"),
            "globale (bande entière)"
        );
        assert_eq!(panel_title(FR, "20-100 Hz (RMS)"), "20-100 Hz (RMS)");
        assert_eq!(
            panel_title(FR, "0-20 Hz (log variance)"),
            "0-20 Hz (variance log)"
        );
    }

    #[test]
    fn the_inspection_statistics() {
        let text = "Location index: 400\nPosition: 640.00 m\nWindow: 0.5-10 s\nSamples: 20000\n\
                    Rate: 2000 Hz\nMean: 4.26326e-18\nStd: 0.109433\nMin: -0.352283\nMax: 0.320216";
        assert_eq!(
            stats(FR, text),
            "Index de l'emplacement\u{a0}: 400\nPosition\u{a0}: 640,00 m\nFenêtre\u{a0}: 0,5-10 s\n\
             Échantillons\u{a0}: 20000\nCadence\u{a0}: 2000 Hz\nMoyenne\u{a0}: 4,26326e-18\n\
             Écart-type\u{a0}: 0,109433\nMin\u{a0}: -0,352283\nMax\u{a0}: 0,320216"
        );
    }

    #[test]
    fn summary_values() {
        assert_eq!(trig_frequency(FR, "2,000 Hz"), "2\u{a0}000 Hz");
        assert_eq!(
            trig_frequency(FR, "75,000 Hz (actual 75,007.5008 Hz, +100.0 ppm)"),
            "75\u{a0}000 Hz (réelle 75\u{a0}007,5008 Hz, +100,0 ppm)"
        );
        assert_eq!(
            trig_frequency(FR, "0 Hz (actual 2,000 Hz)"),
            "0 Hz (réelle 2\u{a0}000 Hz)"
        );
        assert_eq!(
            time_correction(
                FR,
                "cardclock-v1: corrected, +0.098 ms vs the count (tracking, +/-61 us)"
            ),
            "cardclock-v1\u{a0}: corrigé, +0,098 ms par rapport au comptage (tracking, +/-61 us)"
        );
        assert_eq!(
            time_correction(FR, "cardclock-v1: corrected, start time"),
            "cardclock-v1\u{a0}: corrigé, heure de début"
        );
        assert_eq!(
            time_correction(FR, "none: count (no time map)"),
            "none\u{a0}: comptage (pas de table de temps)"
        );
        assert_eq!(time_correction(FR, "odd: other"), "odd: other");
        assert_eq!(
            compression(
                FR,
                "ZSTD_LOSSY_1E4 (phase on a 1.221e-04 rad grid = 1.347e-02 nm/m, error <= 6.104e-05 rad = 6.737e-03 nm/m)"
            ),
            "ZSTD_LOSSY_1E4 (phase sur une grille de 1,221e-04 rad = 1,347e-02 nm/m, erreur ≤ 6,104e-05 rad = 6,737e-03 nm/m)"
        );
        assert_eq!(compression(FR, "ZSTD"), "ZSTD");
        assert_eq!(timestamp(FR, "(none)"), "(aucun)");
        assert_eq!(stage(FR, "reading"), "lecture");
    }

    #[test]
    fn the_info_text_is_translated_line_by_line_and_aligned() {
        let text = "File: /d/phase.dat\n  Mode           : phase\n  Quantity       : strain (nm/m)\n\
                    \x20 Gauge length   : 9.6 m\n  Strain         : nm/m = rad x 11.5 (= 110.37 / 9.6 m)\n\
                    \x20 Pulses         : 20,000\n  Duration       : 10.000 s (file: 10 s)\n\
                    \x20 Line size      : 800 samples/pulse\n  Positions      : 800 per pulse\n\
                    \x20 Trig frequency : 2,000 Hz\n  Sample size    : 4 B\n  Flags          : 0x000 (0)\n\
                    \n  Stats for channel 'phase' -- strain (nm/m) over 1,000 pulses (3 NaN/inf samples left out):\n\
                    \x20   min=-1.616  p1=-0.4037  p50=+0.04395  p99=+0.4856  max=+1.62\n\
                    \x20   mean=+0.04603  std=0.1482\n";
        let fr = info(FR, text);
        let lines: Vec<&str> = fr.lines().collect();
        assert_eq!(lines[0], "Fichier\u{a0}: /d/phase.dat");
        // The longest French key sets the column: "Taille d'échantillon".
        assert_eq!(lines[1], "  Mode                \u{a0}: phase");
        assert_eq!(lines[2], "  Grandeur            \u{a0}: déformation (nm/m)");
        assert_eq!(lines[3], "  Longueur de jauge   \u{a0}: 9,6 m");
        assert_eq!(
            lines[4],
            "  Déformation         \u{a0}: nm/m = rad × 11,5 (= 110,37 / 9,6 m)"
        );
        assert_eq!(lines[5], "  Tirs                \u{a0}: 20\u{a0}000");
        assert_eq!(
            lines[6],
            "  Durée               \u{a0}: 10,000 s (fichier\u{a0}: 10 s)"
        );
        assert_eq!(
            lines[7],
            "  Taille de ligne     \u{a0}: 800 échantillons/tir"
        );
        assert_eq!(lines[8], "  Positions           \u{a0}: 800 par tir");
        assert_eq!(lines[9], "  Fréquence de tir    \u{a0}: 2\u{a0}000 Hz");
        assert_eq!(lines[10], "  Taille d'échantillon\u{a0}: 4 B");
        assert_eq!(lines[11], "  Indicateurs         \u{a0}: 0x000 (0)");
        assert_eq!(lines[12], "");
        assert_eq!(
            lines[13],
            "  Statistiques du canal «\u{a0}phase\u{a0}» — déformation (nm/m) sur 1\u{a0}000 tirs \
             (3 échantillons NaN/inf écartés)\u{a0}:"
        );
        assert_eq!(
            lines[14],
            "    min=-1,616  p1=-0,4037  p50=+0,04395  p99=+0,4856  max=+1,62"
        );
        assert_eq!(lines[15], "    moyenne=+0,04603  écart-type=0,1482");
        assert!(fr.ends_with('\n'));
        // An unknown key keeps its words and its value.
        let odd = info(FR, "  Brand new key  : 12.5 widgets\n");
        assert_eq!(odd, "  Brand new key  : 12.5 widgets\n");
    }

    #[test]
    fn option_names_and_helps() {
        assert_eq!(option_name(Lang::En, "max_time_bins"), "Max time bins");
        assert_eq!(option_name(FR, "max_time_bins"), "Cellules de temps max");
        assert_eq!(option_name(FR, "brand_new_option"), "Brand new option");
        assert_eq!(
            help(FR, "Skip the first seconds."),
            "Ignore les premières secondes."
        );
        assert_eq!(
            help(Lang::En, "Skip the first seconds."),
            "Skip the first seconds."
        );
        // Worded otherwise by a newer CLI: its own words, never a stale translation.
        assert_eq!(
            help(FR, "Skip the first seconds (s)."),
            "Skip the first seconds (s)."
        );
    }
}
