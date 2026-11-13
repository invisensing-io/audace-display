//! Values to colours, as matplotlib's `Normalize` + `Colormap` map them.
//!
//! The worker sends the colormap the CLI would draw with as a lookup table
//! (matplotlib's own, `N` entries) with its under / over / bad colours; the
//! colour limits come from the CLI too. The arithmetic follows matplotlib's
//! for a float32 image: `x = (v - vmin) / (vmax - vmin)` in float32, then
//! `i = floor(x * N)`, `x == 1` on the last entry, below 0 the under colour,
//! at or above `N` the over colour, NaN the bad colour.

use bevy_egui::egui::Color32;

#[derive(Debug, Clone, PartialEq)]
pub struct Colormap {
    pub name: String,
    lut: Vec<[u8; 4]>,
    under: [u8; 4],
    over: [u8; 4],
    bad: [u8; 4],
}

impl Colormap {
    /// From the worker's `(N, 4)` uint8 table and its special colours.
    pub fn new(name: &str, lut_bytes: &[u8], under: [u8; 4], over: [u8; 4], bad: [u8; 4]) -> Result<Self, String> {
        if lut_bytes.is_empty() || !lut_bytes.len().is_multiple_of(4) {
            return Err(format!("colormap '{name}': a lookup table of {} bytes", lut_bytes.len()));
        }
        let lut = lut_bytes.chunks_exact(4).map(|c| [c[0], c[1], c[2], c[3]]).collect();
        Ok(Colormap { name: name.to_string(), lut, under, over, bad })
    }

    pub fn len(&self) -> usize {
        self.lut.len()
    }

    /// The colour of entry `i` of the table.
    pub fn entry(&self, i: usize) -> Color32 {
        to_color(self.lut[i.min(self.lut.len() - 1)])
    }

    /// The colour of `v` between `vmin` and `vmax`.
    pub fn rgba(&self, v: f32, norm: &Norm) -> [u8; 4] {
        if v.is_nan() {
            return self.bad;
        }
        let n = self.lut.len() as f32;
        let mut x = norm.unit(v) * n;
        if x == n {
            x = n - 1.0;
        }
        if x < 0.0 {
            self.under
        } else if x >= n {
            self.over
        } else {
            self.lut[x as usize]
        }
    }

    pub fn color(&self, v: f32, norm: &Norm) -> Color32 {
        to_color(self.rgba(v, norm))
    }
}

fn to_color(c: [u8; 4]) -> Color32 {
    Color32::from_rgba_unmultiplied(c[0], c[1], c[2], c[3])
}

/// matplotlib's `Normalize(vmin, vmax)` applied to float32 data.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Norm {
    vmin: f32,
    span: f32,
}

impl Norm {
    pub fn new(vmin: f64, vmax: f64) -> Norm {
        Norm { vmin: vmin as f32, span: (vmax - vmin) as f32 }
    }

    /// `v` on the unit scale (0 at `vmin`, 1 at `vmax`); 0 everywhere when
    /// the limits are equal (or reversed), as matplotlib draws it.
    pub fn unit(&self, v: f32) -> f32 {
        if self.span.is_nan() || self.span <= 0.0 {
            return 0.0;
        }
        (v - self.vmin) / self.span
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gray(n: usize) -> Colormap {
        let lut: Vec<u8> = (0..n).flat_map(|i| [i as u8, i as u8, i as u8, 255]).collect();
        Colormap::new("gray", &lut, [1, 2, 3, 255], [4, 5, 6, 255], [0, 0, 0, 0]).unwrap()
    }

    #[test]
    fn the_limits_map_to_the_ends_of_the_table() {
        let c = gray(256);
        let norm = Norm::new(-1.0, 1.0);
        assert_eq!(c.rgba(-1.0, &norm)[0], 0);
        assert_eq!(c.rgba(1.0, &norm)[0], 255, "x == 1 is the last entry, not over");
        assert_eq!(c.rgba(0.0, &norm)[0], 128);
    }

    #[test]
    fn out_of_range_and_nan_take_the_special_colours() {
        let c = gray(256);
        let norm = Norm::new(0.0, 1.0);
        assert_eq!(c.rgba(-0.001, &norm), [1, 2, 3, 255]);
        assert_eq!(c.rgba(1.001, &norm), [4, 5, 6, 255]);
        assert_eq!(c.rgba(f32::NAN, &norm), [0, 0, 0, 0]);
        assert_eq!(c.rgba(f32::INFINITY, &norm), [4, 5, 6, 255]);
    }

    #[test]
    fn bins_are_floored_like_matplotlib() {
        // N = 4: [0, .25) -> 0, [.25, .5) -> 1, [.5, .75) -> 2, [.75, 1] -> 3.
        let c = gray(4);
        let norm = Norm::new(0.0, 1.0);
        let idx = |v: f32| c.rgba(v, &norm)[0];
        assert_eq!([idx(0.0), idx(0.2499), idx(0.25), idx(0.5), idx(0.7499), idx(0.75), idx(1.0)], [0, 0, 1, 2, 2, 3, 3]);
    }

    #[test]
    fn equal_limits_draw_the_first_colour() {
        let c = gray(256);
        let norm = Norm::new(-12.0, -12.0);
        assert_eq!(c.rgba(-12.0, &norm)[0], 0);
        assert_eq!(c.rgba(5.0, &norm)[0], 0);
    }

    #[test]
    fn refuses_a_ragged_table() {
        assert!(Colormap::new("x", &[1, 2, 3], [0; 4], [0; 4], [0; 4]).is_err());
        assert!(Colormap::new("x", &[], [0; 4], [0; 4], [0; 4]).is_err());
    }
}
