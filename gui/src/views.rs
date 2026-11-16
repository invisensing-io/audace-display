//! The worker's results, decoded for display: images as textures, curves as
//! `f64` points.

use bevy_egui::egui::{self, Color32, ColorImage, TextureHandle, TextureOptions};

use crate::colormap::{Colormap, Norm};
use crate::i18n::WorkerText;
use crate::params::View;
use crate::protocol::{
    Array, ArrayData, BandsMeta, ColormapMeta, FftMeta, HeatmapMeta, InfoMeta, InspectMeta,
    Message, ProtocolError, ScopeMeta, TraceMeta, warning_texts,
};

/// Largest texture side used: an image above it is split into tiles (GPUs
/// guarantee 8192, WebGL-class adapters 2048).
pub const TILE: usize = 2048;

/// One texture of an image: rows `r0..r1`, columns `c0..c1`.
pub struct Tile {
    pub texture: TextureHandle,
    pub r0: usize,
    pub r1: usize,
    pub c0: usize,
    pub c1: usize,
}

/// A colour-mapped field: rows are time (row 0 = earliest), columns distance.
/// A waterfall draws it with its last row, the newest, at the top, as the
/// CLI's figure does (`origin="lower"`): time increases upwards.
pub struct Image2d {
    pub title: String,
    pub label: String,
    pub rows: usize,
    pub cols: usize,
    pub data: Vec<f32>,
    pub t_extent: [f64; 2],
    pub d_extent: [f64; 2],
    pub vmin: f64,
    pub vmax: f64,
    pub cmap: Colormap,
    pub tiles: Vec<Tile>,
}

impl Image2d {
    #[allow(clippy::too_many_arguments)]
    fn new(
        ctx: &egui::Context,
        name: &str,
        title: String,
        label: String,
        data: Array,
        lut: Array,
        extents: ([f64; 2], [f64; 2]),
        limits: (Option<f64>, Option<f64>),
        colormap: &ColormapMeta,
    ) -> Result<Image2d, String> {
        let (rows, cols) = match data.shape[..] {
            [r, c] => (r, c),
            _ => {
                return Err(format!(
                    "{name}: a 2-D image was expected, got the shape {:?}",
                    data.shape
                ));
            }
        };
        let values = data
            .data
            .into_f32()
            .ok_or_else(|| format!("{name}: float32 values expected"))?;
        let ArrayData::U8(lut_bytes) = lut.data else {
            return Err(format!("{name}: the colormap table must be bytes"));
        };
        let cmap = Colormap::new(
            &colormap.cmap,
            &lut_bytes,
            colormap.under,
            colormap.over,
            colormap.bad,
        )?;
        // Limits the CLI could not compute (an all-NaN field) draw on [0, 1].
        let vmin = limits.0.unwrap_or(0.0);
        let vmax = limits.1.unwrap_or(1.0);
        let mut img = Image2d {
            title,
            label,
            rows,
            cols,
            data: values,
            t_extent: extents.0,
            d_extent: extents.1,
            vmin,
            vmax,
            cmap,
            tiles: Vec::new(),
        };
        img.upload(ctx, name);
        Ok(img)
    }

    fn upload(&mut self, ctx: &egui::Context, name: &str) {
        let norm = Norm::new(self.vmin, self.vmax);
        self.tiles.clear();
        for r0 in (0..self.rows).step_by(TILE) {
            let r1 = (r0 + TILE).min(self.rows);
            for c0 in (0..self.cols).step_by(TILE) {
                let c1 = (c0 + TILE).min(self.cols);
                let pixels = self.tile_pixels(r0..r1, c0..c1, &norm);
                let image = ColorImage::new([c1 - c0, r1 - r0], pixels);
                let texture =
                    ctx.load_texture(format!("{name}-{r0}-{c0}"), image, TextureOptions::NEAREST);
                self.tiles.push(Tile {
                    texture,
                    r0,
                    r1,
                    c0,
                    c1,
                });
            }
        }
    }

    /// The pixels of the texture of rows `rows`, columns `cols`, top row
    /// first. A texture is drawn with its first row at the top and the plot's
    /// time axis increases upwards, so the rows go from the last (the newest)
    /// down to the first: the newest line of the file is at the top.
    pub fn tile_pixels(
        &self,
        rows: std::ops::Range<usize>,
        cols: std::ops::Range<usize>,
        norm: &Norm,
    ) -> Vec<Color32> {
        let mut pixels = Vec::with_capacity(rows.len() * cols.len());
        for r in rows.rev() {
            let row = &self.data[r * self.cols + cols.start..r * self.cols + cols.end];
            pixels.extend(row.iter().map(|&v| self.cmap.color(v, norm)));
        }
        pixels
    }

    /// Plot rectangle of a tile: `(distance min, distance max, time min, time max)`.
    pub fn tile_extent(&self, tile: &Tile) -> (f64, f64, f64, f64) {
        let [d0, d1] = self.d_extent;
        let [t0, t1] = self.t_extent;
        let dc = (d1 - d0) / self.cols.max(1) as f64;
        let tr = (t1 - t0) / self.rows.max(1) as f64;
        (
            d0 + tile.c0 as f64 * dc,
            d0 + tile.c1 as f64 * dc,
            t0 + tile.r0 as f64 * tr,
            t0 + tile.r1 as f64 * tr,
        )
    }

    /// The cell under `(distance, time)`, as `(row, column, value)`.
    pub fn cell_at(&self, distance: f64, time: f64) -> Option<(usize, usize, f32)> {
        let [d0, d1] = self.d_extent;
        let [t0, t1] = self.t_extent;
        if !(d1 > d0 && t1 > t0) || distance < d0 || distance >= d1 || time < t0 || time >= t1 {
            return None;
        }
        let c = (((distance - d0) / (d1 - d0)) * self.cols as f64) as usize;
        let r = (((time - t0) / (t1 - t0)) * self.rows as f64) as usize;
        let (r, c) = (r.min(self.rows - 1), c.min(self.cols - 1));
        Some((r, c, self.data[r * self.cols + c]))
    }

    pub fn color_at_unit(&self, u: f32) -> Color32 {
        let i = ((u.clamp(0.0, 1.0)) * (self.cmap.len() as f32 - 1.0)).round() as usize;
        self.cmap.entry(i)
    }

    /// A grey image of `rows` x `cols` row-major values on `[vmin, vmax]`,
    /// uploaded to `ctx`: black at `vmin`, white at `vmax`.
    #[cfg(test)]
    pub fn grey_for_tests(
        ctx: &egui::Context,
        (rows, cols): (usize, usize),
        data: Vec<f32>,
        (t_extent, d_extent): ([f64; 2], [f64; 2]),
        (vmin, vmax): (f64, f64),
    ) -> Image2d {
        assert_eq!(data.len(), rows * cols);
        let lut: Vec<u8> = (0..=255u8).flat_map(|v| [v, v, v, 255]).collect();
        let cmap = Colormap::new("gray", &lut, [0, 0, 0, 255], [255; 4], [255, 0, 0, 255])
            .expect("a grey table");
        let mut img = Image2d {
            title: String::new(),
            label: String::new(),
            rows,
            cols,
            data,
            t_extent,
            d_extent,
            vmin,
            vmax,
            cmap,
            tiles: Vec::new(),
        };
        img.upload(ctx, "test");
        img
    }
}

/// One curve of a line plot.
pub struct Curve {
    pub label: Option<String>,
    pub x: Vec<f64>,
    pub y: Vec<f64>,
}

pub struct FftResult {
    pub meta: FftMeta,
    pub curves: Vec<Curve>,
}

pub struct TraceResult {
    pub meta: TraceMeta,
    pub times: Vec<f64>,
    pub lo: Vec<f64>,
    pub hi: Vec<f64>,
}

pub struct InspectResult {
    pub meta: InspectMeta,
    pub times: Vec<f64>,
    pub waveform: Vec<f64>,
    pub freqs: Vec<f64>,
    pub magnitude: Vec<f64>,
}

#[derive(Clone)]
pub struct ScopeFrame {
    pub local: u64,
    pub line: u64,
    pub t: f64,
    pub x: Vec<f64>,
    pub y: Vec<f64>,
}

#[derive(Clone)]
pub struct ScopeResult {
    pub meta: ScopeMeta,
    pub first: ScopeFrame,
}

pub enum ViewResult {
    Info(String),
    Heatmap(Image2d),
    Bands { title: String, panels: Vec<Image2d> },
    Fft(FftResult),
    Trace(TraceResult),
    Inspect(InspectResult),
    Scope(ScopeResult),
}

fn err(e: ProtocolError) -> String {
    e.to_string()
}

fn take_f64(msg: &mut Message, name: &str) -> Result<Vec<f64>, String> {
    Ok(msg.take(name).map_err(err)?.data.to_f64())
}

/// A scope frame message (`scope` or `scope_frame`).
pub fn decode_frame(
    msg: &mut Message,
    local: u64,
    line: u64,
    t: f64,
) -> Result<ScopeFrame, String> {
    Ok(ScopeFrame {
        local,
        line,
        t,
        x: take_f64(msg, "x")?,
        y: take_f64(msg, "y")?,
    })
}

/// Decodes the result of a render; returns it with the warnings it carried.
pub fn decode(
    view: View,
    mut msg: Message,
    ctx: &egui::Context,
) -> Result<(ViewResult, Vec<WorkerText>), String> {
    let kind = msg.kind().to_string();
    if kind != view.cli_name() {
        return Err(format!(
            "the worker answered a {} request with '{kind}'",
            view.cli_name()
        ));
    }
    Ok(match view {
        View::Info => {
            let m: InfoMeta = msg.parse().map_err(err)?;
            let warnings = warning_texts(&m.warnings, &m.warning_codes);
            (ViewResult::Info(m.text), warnings)
        }
        View::Heatmap => {
            let m: HeatmapMeta = msg.parse().map_err(err)?;
            let data = msg.take("data").map_err(err)?;
            let lut = msg.take("lut").map_err(err)?;
            let img = Image2d::new(
                ctx,
                "heatmap",
                m.title,
                m.label,
                data,
                lut,
                (m.t_extent, m.d_extent),
                (m.vmin, m.vmax),
                &m.colormap,
            )?;
            (
                ViewResult::Heatmap(img),
                warning_texts(&m.warnings, &m.warning_codes),
            )
        }
        View::Bands => {
            let m: BandsMeta = msg.parse().map_err(err)?;
            let mut panels = Vec::with_capacity(m.panels.len());
            for (i, p) in m.panels.iter().enumerate() {
                let data = msg.take(&format!("panel{i}")).map_err(err)?;
                let lut = msg.take(&format!("lut{i}")).map_err(err)?;
                panels.push(Image2d::new(
                    ctx,
                    &format!("band{i}"),
                    p.title.clone(),
                    p.label.clone(),
                    data,
                    lut,
                    (m.t_extent, m.d_extent),
                    (p.vmin, p.vmax),
                    &p.colormap,
                )?);
            }
            let warnings = warning_texts(&m.warnings, &m.warning_codes);
            (
                ViewResult::Bands {
                    title: m.title,
                    panels,
                },
                warnings,
            )
        }
        View::Fft => {
            let m: FftMeta = msg.parse().map_err(err)?;
            let freqs = take_f64(&mut msg, "freqs")?;
            let mut curves = Vec::new();
            for (i, label) in m.curves.iter().enumerate() {
                curves.push(Curve {
                    label: label.clone(),
                    x: freqs.clone(),
                    y: take_f64(&mut msg, &format!("curve{i}"))?,
                });
            }
            let warnings = warning_texts(&m.warnings, &m.warning_codes);
            (ViewResult::Fft(FftResult { meta: m, curves }), warnings)
        }
        View::Trace => {
            let m: TraceMeta = msg.parse().map_err(err)?;
            let r = TraceResult {
                times: take_f64(&mut msg, "times")?,
                lo: take_f64(&mut msg, "lo")?,
                hi: take_f64(&mut msg, "hi")?,
                meta: m,
            };
            let warnings = warning_texts(&r.meta.warnings, &r.meta.warning_codes);
            (ViewResult::Trace(r), warnings)
        }
        View::Inspect => {
            let m: InspectMeta = msg.parse().map_err(err)?;
            let r = InspectResult {
                times: take_f64(&mut msg, "times")?,
                waveform: take_f64(&mut msg, "waveform")?,
                freqs: take_f64(&mut msg, "freqs")?,
                magnitude: take_f64(&mut msg, "magnitude")?,
                meta: m,
            };
            let warnings = warning_texts(&r.meta.warnings, &r.meta.warning_codes);
            (ViewResult::Inspect(r), warnings)
        }
        View::Scope => {
            let m: ScopeMeta = msg.parse().map_err(err)?;
            let first = decode_frame(&mut msg, m.frame.local, m.frame.line, m.frame.t)?;
            let warnings = warning_texts(&m.warnings, &m.warning_codes);
            (ViewResult::Scope(ScopeResult { meta: m, first }), warnings)
        }
    })
}

/// A peak-preserving subset of a curve for drawing: at most two points
/// (min, max) per bin over the visible `x` range. The data is untouched;
/// only what is sent to the painter is reduced, so millions of spectrum bins
/// draw as fast as a few thousand. A missing sample (NaN, a strain rate's
/// first line) is not drawn.
pub fn envelope(x: &[f64], y: &[f64], x_range: (f64, f64), bins: usize) -> Vec<[f64; 2]> {
    let n = x.len().min(y.len());
    if n == 0 {
        return Vec::new();
    }
    // Visible slice, one point of margin each side (x is sorted).
    let lo = x[..n].partition_point(|&v| v < x_range.0).saturating_sub(1);
    let hi = (x[..n].partition_point(|&v| v <= x_range.1) + 1).min(n);
    if hi <= lo {
        return Vec::new();
    }
    let count = hi - lo;
    if count <= 2 * bins.max(1) {
        return (lo..hi)
            .filter(|&i| y[i].is_finite())
            .map(|i| [x[i], y[i]])
            .collect();
    }
    let bins = bins.max(1);
    let mut out = Vec::with_capacity(2 * bins);
    for b in 0..bins {
        let s = lo + b * count / bins;
        let e = lo + (b + 1) * count / bins;
        if e <= s {
            continue;
        }
        let Some(first) = (s..e).find(|&i| y[i].is_finite()) else {
            continue;
        };
        let (mut imin, mut imax) = (first, first);
        for i in first..e {
            if !y[i].is_finite() {
                continue;
            }
            if y[i] < y[imin] {
                imin = i;
            }
            if y[i] > y[imax] {
                imax = i;
            }
        }
        let (a, b2) = if imin <= imax {
            (imin, imax)
        } else {
            (imax, imin)
        };
        out.push([x[a], y[a]]);
        if b2 != a {
            out.push([x[b2], y[b2]]);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Row `r` of a `rows` x `cols` image holds the value `r` everywhere.
    fn rows_numbered(rows: usize, cols: usize) -> Vec<f32> {
        (0..rows * cols).map(|i| (i / cols) as f32).collect()
    }

    #[test]
    fn a_texture_holds_the_newest_row_first() {
        let ctx = egui::Context::default();
        let img = Image2d::grey_for_tests(
            &ctx,
            (4, 3),
            rows_numbered(4, 3),
            ([0.0, 4.0], [0.0, 30.0]),
            (0.0, 3.0),
        );
        let norm = Norm::new(img.vmin, img.vmax);
        let px = img.tile_pixels(0..4, 0..3, &norm);
        let shade = |r: usize| img.cmap.color(r as f32, &norm);
        // Top row of the texture: the last row of the data (the newest line).
        assert!(px[..3].iter().all(|&c| c == shade(3)), "{:?}", &px[..3]);
        assert!(px[9..].iter().all(|&c| c == shade(0)), "{:?}", &px[9..]);
        assert_ne!(shade(3), shade(0));
        // A tile covers its own rows, newest first.
        let part = img.tile_pixels(1..3, 1..2, &norm);
        assert_eq!(part, vec![shade(2), shade(1)]);
    }

    #[test]
    fn the_last_tile_covers_the_latest_times() {
        let ctx = egui::Context::default();
        let rows = TILE + 5;
        let img = Image2d::grey_for_tests(
            &ctx,
            (rows, 1),
            rows_numbered(rows, 1),
            ([10.0, 10.0 + rows as f64], [0.0, 1.0]),
            (0.0, rows as f64 - 1.0),
        );
        assert_eq!(img.tiles.len(), 2);
        let (first, last) = (&img.tiles[0], &img.tiles[1]);
        assert_eq!(
            (first.r0, first.r1, last.r0, last.r1),
            (0, TILE, TILE, rows)
        );
        let (_, _, f0, f1) = img.tile_extent(first);
        let (_, _, l0, l1) = img.tile_extent(last);
        assert_eq!(
            (f0, f1, l0, l1),
            (
                10.0,
                10.0 + TILE as f64,
                10.0 + TILE as f64,
                10.0 + rows as f64
            )
        );
    }

    #[test]
    fn envelope_keeps_short_curves_whole() {
        let x: Vec<f64> = (0..10).map(f64::from).collect();
        let y: Vec<f64> = x.iter().map(|v| v * v).collect();
        let e = envelope(&x, &y, (f64::NEG_INFINITY, f64::INFINITY), 100);
        assert_eq!(e.len(), 10);
        assert_eq!(e[3], [3.0, 9.0]);
    }

    #[test]
    fn envelope_keeps_the_peaks_of_long_curves() {
        let n = 1_000_000;
        let x: Vec<f64> = (0..n).map(|i| i as f64).collect();
        let mut y = vec![0.0; n];
        y[123_457] = 5.0;
        y[876_543] = -7.0;
        let e = envelope(&x, &y, (0.0, n as f64), 500);
        assert!(e.len() <= 1000);
        assert!(e.contains(&[123_457.0, 5.0]) && e.contains(&[876_543.0, -7.0]));
        assert!(
            e.windows(2).all(|w| w[0][0] <= w[1][0]),
            "points stay in x order"
        );
    }

    #[test]
    fn envelope_leaves_missing_samples_out() {
        let x = [0.0, 1.0, 2.0, 3.0];
        let y = [f64::NAN, 1.0, f64::INFINITY, 3.0];
        assert_eq!(
            envelope(&x, &y, (0.0, 3.0), 100),
            vec![[1.0, 1.0], [3.0, 3.0]]
        );
        let x: Vec<f64> = (0..1000).map(f64::from).collect();
        let mut y = vec![f64::NAN; 1000];
        y[500] = 2.0;
        assert_eq!(envelope(&x, &y, (0.0, 999.0), 10), vec![[500.0, 2.0]]);
    }

    #[test]
    fn envelope_restricts_to_the_visible_range() {
        let x: Vec<f64> = (0..1000).map(f64::from).collect();
        let y = x.clone();
        let e = envelope(&x, &y, (100.0, 200.0), 1000);
        assert_eq!(e.first().unwrap()[0], 99.0);
        assert_eq!(e.last().unwrap()[0], 201.0);
    }
}
