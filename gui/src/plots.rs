//! Plot widgets: waterfalls with their colour bar, line plots. Waterfalls put
//! time on the vertical axis running downwards and distance on the
//! horizontal axis.

use std::f32::consts::FRAC_PI_2;

use bevy_egui::egui::{self, Align2, Color32, FontId, Pos2, Rect, Sense, Stroke, Ui, Vec2, epaint::TextShape, vec2};
use egui_plot::{FilledArea, HoverPosition, Legend, Line, Plot, PlotBounds, PlotImage, PlotPoint, PlotPoints, Text, VLine};

use crate::app::PlotNav;
use crate::views::{Image2d, envelope};

/// `x` with `sig` significant digits, the way `%g` prints it.
pub fn fmt_g(x: f64, sig: usize) -> String {
    if !x.is_finite() {
        return format!("{x}");
    }
    if x == 0.0 {
        return "0".into();
    }
    let sig = sig.max(1);
    let exp = x.abs().log10().floor() as i32;
    if exp < -4 || exp >= sig as i32 {
        let s = format!("{:.*e}", sig - 1, x);
        // 1.500e3 -> 1.5e3
        match s.split_once('e') {
            Some((m, e)) if m.contains('.') => format!("{}e{e}", m.trim_end_matches('0').trim_end_matches('.')),
            _ => s,
        }
    } else {
        let decimals = (sig as i32 - 1 - exp).max(0) as usize;
        let s = format!("{x:.decimals$}");
        if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s }
    }
}

fn bounds_of(b: &PlotBounds) -> [f64; 4] {
    [b.min()[0], b.min()[1], b.max()[0], b.max()[1]]
}

fn close(a: [f64; 4], b: [f64; 4]) -> bool {
    let span = ((a[2] - a[0]).abs() + (a[3] - a[1]).abs()).max(f64::MIN_POSITIVE);
    a.iter().zip(b).all(|(x, y)| (x - y).abs() <= 1e-9 * span)
}

/// What a zoomable plot reports after it is drawn.
#[derive(Default)]
pub struct PlotOutcome {
    /// The user settled on new bounds `[x0, y0, x1, y1]`.
    pub new_bounds: Option<[f64; 4]>,
    /// The user double-clicked: back to the unzoomed window.
    pub reset: bool,
    /// The cell under the pointer: `(distance, time, value)`.
    pub hover: Option<(f64, f64, Option<f32>)>,
}

/// Follows a plot's bounds: after a fit they are the expected ones; a change
/// the user made, once the pointer is released, is reported.
fn track_bounds(ui: &Ui, nav: &mut PlotNav, bounds: [f64; 4], fitted: bool, x_only: bool) -> Option<[f64; 4]> {
    if fitted {
        nav.expected = Some(bounds);
        return None;
    }
    let interacting = ui.input(|i| i.pointer.any_down());
    match nav.expected {
        None => {
            nav.expected = Some(bounds);
            None
        }
        Some(e) => {
            let (e2, b2) = if x_only { ([e[0], 0.0, e[2], 1.0], [bounds[0], 0.0, bounds[2], 1.0]) } else { (e, bounds) };
            if !interacting && !close(e2, b2) {
                nav.expected = Some(bounds);
                Some(bounds)
            } else {
                None
            }
        }
    }
}

/// A waterfall: the image in plot coordinates (distance, time downwards).
pub fn heatmap(ui: &mut Ui, id: &str, img: &Image2d, nav: &mut PlotNav, fit: bool, link: Option<&str>, size: Vec2) -> PlotOutcome {
    let label = img.label.clone();
    let mut plot = Plot::new(id)
        .width(size.x)
        .height(size.y)
        .invert_y(true)
        .x_axis_label("Distance (m)")
        .y_axis_label("Time (s)")
        .allow_double_click_reset(false)
        .show_grid(false)
        .label_formatter(move |hp: &HoverPosition<'_>| {
            let p = match hp {
                HoverPosition::NearDataPoint { position, .. } | HoverPosition::Elsewhere { position } => position,
            };
            let value = img.cell_at(p.x, p.y).map(|(_, _, v)| fmt_g(v as f64, 5)).unwrap_or_else(|| "-".into());
            Some(format!("d = {} m\nt = {} s\n{} = {value}", fmt_g(p.x, 6), fmt_g(p.y, 6), label))
        });
    if let Some(group) = link {
        plot = plot.link_axis(group.to_string(), [true, true]).link_cursor(group.to_string(), [true, true]);
    }
    let [d0, d1] = img.d_extent;
    let [t0, t1] = img.t_extent;
    let resp = plot.show(ui, |pui| {
        if fit {
            pui.set_plot_bounds(PlotBounds::from_min_max([d0, t0], [d1, t1]));
        }
        for tile in &img.tiles {
            let (x0, x1, y0, y1) = img.tile_extent(tile);
            pui.image(PlotImage::new(
                "",
                tile.texture.id(),
                PlotPoint::new((x0 + x1) / 2.0, (y0 + y1) / 2.0),
                vec2((x1 - x0) as f32, (y1 - y0) as f32),
            ));
        }
        pui.pointer_coordinate()
    });
    let mut out = PlotOutcome {
        hover: resp.inner.map(|p| (p.x, p.y, img.cell_at(p.x, p.y).map(|c| c.2))),
        reset: resp.response.double_clicked(),
        ..PlotOutcome::default()
    };
    out.new_bounds = track_bounds(ui, nav, bounds_of(resp.transform.bounds()), fit, false);
    out
}

/// The colour bar of an image: `vmax` at the top, `vmin` at the bottom, the
/// label written upwards beside it.
pub fn colorbar(ui: &mut Ui, img: &Image2d, height: f32) {
    let width = 92.0;
    let (rect, _) = ui.allocate_exact_size(vec2(width, height), Sense::hover());
    let painter = ui.painter_at(rect);
    let text_color = ui.visuals().text_color();
    let bar = Rect::from_min_size(rect.min + vec2(4.0, 10.0), vec2(16.0, (height - 20.0).max(10.0)));
    let steps = 128;
    for i in 0..steps {
        let u0 = i as f32 / steps as f32;
        let u1 = (i + 1) as f32 / steps as f32;
        let r = Rect::from_min_max(
            Pos2::new(bar.left(), bar.bottom() - u1 * bar.height()),
            Pos2::new(bar.right(), bar.bottom() - u0 * bar.height() + 0.5),
        );
        painter.rect_filled(r, 0.0, img.color_at_unit((u0 + u1) / 2.0));
    }
    painter.rect_stroke(bar, 0.0, Stroke::new(1.0, text_color.gamma_multiply(0.6)), egui::StrokeKind::Outside);
    let font = FontId::proportional(11.0);
    let ticks = 5;
    for k in 0..ticks {
        let u = k as f64 / (ticks - 1) as f64;
        let v = img.vmin + u * (img.vmax - img.vmin);
        let y = bar.bottom() - u as f32 * bar.height();
        painter.line_segment([Pos2::new(bar.right(), y), Pos2::new(bar.right() + 4.0, y)], Stroke::new(1.0, text_color));
        painter.text(Pos2::new(bar.right() + 6.0, y), Align2::LEFT_CENTER, fmt_g(v, 4), font.clone(), text_color);
    }
    let galley = painter.layout_no_wrap(img.label.clone(), FontId::proportional(12.0), text_color);
    let pos = Pos2::new(rect.right() - 2.0 - galley.size().y, bar.center().y + galley.size().x / 2.0);
    painter.add(TextShape::new(pos, galley, text_color).with_angle(-FRAC_PI_2));
}

/// A time trace: the min/max envelope (the band) and its middle line.
#[allow(clippy::too_many_arguments)]
pub fn trace(
    ui: &mut Ui,
    times: &[f64],
    lo: &[f64],
    hi: &[f64],
    y_label: &str,
    nav: &mut PlotNav,
    fit: bool,
    size: Vec2,
) -> PlotOutcome {
    let plot = Plot::new("trace")
        .width(size.x)
        .height(size.y)
        .x_axis_label("Time (s)")
        .y_axis_label(y_label.to_string())
        .allow_double_click_reset(false);
    let same = lo == hi;
    let bins = (size.x as usize).max(100);
    let resp = plot.show(ui, |pui| {
        if fit && !times.is_empty() {
            let (ymin, ymax) = min_max(lo.iter().chain(hi.iter()).copied());
            let pad = 0.05 * ((ymax - ymin).abs().max(f64::EPSILON));
            pui.set_plot_bounds(PlotBounds::from_min_max(
                [times[0], ymin - pad],
                [*times.last().unwrap(), ymax + pad],
            ));
        }
        let b = pui.plot_bounds();
        let range = (b.min()[0], b.max()[0]);
        if same {
            pui.line(Line::new("trace", PlotPoints::from(envelope(times, lo, range, bins))).width(1.0));
        } else {
            let (xs, los, his) = band(times, lo, hi, range, bins);
            pui.add(FilledArea::new("envelope", &xs, &los, &his).fill_color(Color32::from_rgba_unmultiplied(31, 119, 180, 110)));
            let mid: Vec<f64> = los.iter().zip(&his).map(|(a, b)| 0.5 * (a + b)).collect();
            pui.line(Line::new("trace", PlotPoints::from(xs.iter().zip(&mid).map(|(x, y)| [*x, *y]).collect::<Vec<_>>())).width(0.8));
        }
    });
    PlotOutcome {
        reset: resp.response.double_clicked(),
        new_bounds: track_bounds(ui, nav, bounds_of(resp.transform.bounds()), fit, true),
        hover: None,
    }
}

/// The band between `lo` and `hi`, reduced to `bins` columns over the visible range.
fn band(x: &[f64], lo: &[f64], hi: &[f64], range: (f64, f64), bins: usize) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let n = x.len().min(lo.len()).min(hi.len());
    let a = x[..n].partition_point(|&v| v < range.0).saturating_sub(1);
    let b = (x[..n].partition_point(|&v| v <= range.1) + 1).min(n);
    if b <= a {
        return (vec![], vec![], vec![]);
    }
    let count = b - a;
    if count <= bins * 2 {
        let keep: Vec<usize> = (a..b).filter(|&i| lo[i].is_finite() && hi[i].is_finite()).collect();
        return (keep.iter().map(|&i| x[i]).collect(), keep.iter().map(|&i| lo[i]).collect(), keep.iter().map(|&i| hi[i]).collect());
    }
    let (mut xs, mut los, mut his) = (Vec::with_capacity(bins), Vec::with_capacity(bins), Vec::with_capacity(bins));
    for k in 0..bins {
        let s = a + k * count / bins;
        let e = a + (k + 1) * count / bins;
        if e <= s {
            continue;
        }
        // f64::min / max skip NaN; a column with no finite sample is not drawn.
        let l = lo[s..e].iter().copied().fold(f64::INFINITY, f64::min);
        let h = hi[s..e].iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if l.is_finite() && h.is_finite() {
            xs.push(0.5 * (x[s] + x[e - 1]));
            los.push(l);
            his.push(h);
        }
    }
    (xs, los, his)
}

pub fn min_max(values: impl Iterator<Item = f64>) -> (f64, f64) {
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for v in values.filter(|v| v.is_finite()) {
        lo = lo.min(v);
        hi = hi.max(v);
    }
    if lo > hi { (0.0, 1.0) } else { (lo, hi) }
}

pub const CURVE_COLORS: [Color32; 6] = [
    Color32::from_rgb(31, 119, 180),
    Color32::from_rgb(255, 127, 14),
    Color32::from_rgb(44, 160, 44),
    Color32::from_rgb(214, 39, 40),
    Color32::from_rgb(148, 103, 189),
    Color32::from_rgb(140, 86, 75),
];

/// A line plot of one or more curves sharing `x` (spectra, waveforms).
pub struct LinePlot<'a> {
    pub id: String,
    pub x_label: String,
    pub y_label: String,
    pub curves: Vec<(Option<&'a str>, &'a [f64], &'a [f64])>,
    /// Bounds set when the plot is (re)fitted: `[x0, y0, x1, y1]`.
    pub fit_bounds: Option<[f64; 4]>,
    pub log_y: bool,
    /// A vertical marker and its text (the dominant peak).
    pub marker: Option<(f64, f64, String)>,
}

impl LinePlot<'_> {
    pub fn show(self, ui: &mut Ui, size: Vec2) {
        let legend = self.curves.iter().any(|c| c.0.is_some());
        let log_y = self.log_y;
        let mut plot = Plot::new(self.id.as_str())
            .width(size.x)
            .height(size.y)
            .x_axis_label(self.x_label.clone())
            .y_axis_label(self.y_label.clone());
        if legend {
            plot = plot.legend(Legend::default());
        }
        if log_y {
            plot = plot
                .y_axis_formatter(|mark, _| format!("1e{}", fmt_g(mark.value, 3)))
                .label_formatter(|hp: &HoverPosition<'_>| {
                    let p = match hp {
                        HoverPosition::NearDataPoint { position, .. } | HoverPosition::Elsewhere { position } => position,
                    };
                    Some(format!("x = {}\ny = {}", fmt_g(p.x, 6), fmt_g(10f64.powf(p.y), 5)))
                });
        }
        let bins = (size.x as usize).max(200);
        let fit = self.fit_bounds;
        plot.show(ui, |pui| {
            if let Some([x0, y0, x1, y1]) = fit {
                pui.set_plot_bounds(PlotBounds::from_min_max([x0, y0], [x1, y1]));
            }
            let b = pui.plot_bounds();
            let range = (b.min()[0], b.max()[0]);
            for (i, (label, x, y)) in self.curves.iter().enumerate() {
                let pts = if log_y {
                    let ly: Vec<f64> = y.iter().map(|v| v.max(1e-300).log10()).collect();
                    envelope(x, &ly, range, bins)
                } else {
                    envelope(x, y, range, bins)
                };
                let color = CURVE_COLORS[i % CURVE_COLORS.len()];
                pui.line(Line::new(label.unwrap_or(""), PlotPoints::from(pts)).color(color).width(1.0));
            }
            if let Some((x, y, text)) = &self.marker {
                pui.vline(VLine::new("", *x).color(Color32::from_rgb(214, 39, 40)).width(1.0));
                let y = if log_y { y.max(1e-300).log10() } else { *y };
                pui.text(Text::new("", PlotPoint::new(*x, y), text.clone()).anchor(Align2::LEFT_BOTTOM));
            }
        });
    }
}

/// The fit bounds of curves over `[x0, x1]` (`y` padded by 5 %).
pub fn fit_curves(curves: &[(&[f64], &[f64])], x0: f64, x1: f64, log_y: bool) -> [f64; 4] {
    let ys = curves.iter().flat_map(|(x, y)| {
        x.iter().zip(y.iter()).filter(move |(xv, _)| **xv >= x0 && **xv <= x1).map(move |(_, yv)| {
            if log_y { yv.max(1e-300).log10() } else { *yv }
        })
    });
    let (lo, hi) = min_max(ys);
    let pad = 0.05 * (hi - lo).abs().max(f64::EPSILON);
    [x0, lo - pad, x1, hi + pad]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_like_percent_g() {
        assert_eq!(fmt_g(0.0, 4), "0");
        assert_eq!(fmt_g(1.0, 4), "1");
        assert_eq!(fmt_g(2.795755, 4), "2.796");
        assert_eq!(fmt_g(-12.0, 4), "-12");
        assert_eq!(fmt_g(51.2, 6), "51.2");
        assert_eq!(fmt_g(123456.0, 4), "1.235e5");
        assert_eq!(fmt_g(0.00001234, 3), "1.23e-5");
        assert_eq!(fmt_g(0.001, 3), "0.001");
    }

    #[test]
    fn bounds_compare_relative_to_their_span() {
        assert!(close([0.0, 0.0, 100.0, 10.0], [1e-8, 0.0, 100.0, 10.0]));
        assert!(!close([0.0, 0.0, 100.0, 10.0], [1.0, 0.0, 100.0, 10.0]));
    }

    #[test]
    fn the_band_keeps_extremes_per_column() {
        let x: Vec<f64> = (0..1000).map(f64::from).collect();
        let lo: Vec<f64> = x.iter().map(|v| -v).collect();
        let hi = x.clone();
        let (xs, los, his) = band(&x, &lo, &hi, (0.0, 999.0), 10);
        assert_eq!(xs.len(), 10);
        assert_eq!(los[9], -999.0);
        assert_eq!(his[9], 999.0);
    }

    #[test]
    fn fitting_ignores_points_outside_the_range_and_non_finite_values() {
        let x = [0.0, 1.0, 2.0, 3.0];
        let y = [100.0, 1.0, f64::NAN, 3.0];
        let b = fit_curves(&[(&x, &y)], 0.5, 3.0, false);
        assert_eq!((b[0], b[2]), (0.5, 3.0));
        assert!(b[1] < 1.0 && b[1] > 0.8 && b[3] > 3.0 && b[3] < 3.2);
    }
}
