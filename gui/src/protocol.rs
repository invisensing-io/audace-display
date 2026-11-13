//! The worker protocol: line-delimited JSON with length-framed binary arrays.
//!
//! The worker is `python -m audace_display.gui_worker`; its module docstring
//! is the reference of the protocol. In short: every message is one UTF-8
//! JSON line; a message that carries arrays lists them in `arrays` (name,
//! dtype, shape, nbytes) and exactly the sum of their `nbytes` follows the
//! line's `\n`, the arrays one after the other, C order, little-endian.

use std::collections::BTreeMap;
use std::fmt;
use std::io::{BufRead, Read};

use serde::Deserialize;
use serde_json::Value;

/// The protocol version this viewer speaks.
pub const PROTOCOL: u32 = 1;

/// Longest header line accepted (the `hello` schema is a few tens of kB).
const MAX_LINE_BYTES: usize = 16 * 1024 * 1024;
/// Largest array accepted: a corrupted header must not allocate the world.
const MAX_ARRAY_BYTES: usize = 8 * 1024 * 1024 * 1024;

/// What can go wrong while reading the worker's output.
#[derive(Debug)]
pub enum ProtocolError {
    Io(std::io::Error),
    /// The stream ended in the middle of a message.
    Truncated(String),
    /// A header line that is not the JSON the protocol describes.
    BadHeader(String),
    /// An array descriptor that does not add up.
    BadArray(String),
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ProtocolError::Io(e) => write!(f, "reading the worker: {e}"),
            ProtocolError::Truncated(what) => write!(f, "the worker's output stopped inside {what}"),
            ProtocolError::BadHeader(why) => write!(f, "unreadable message from the worker: {why}"),
            ProtocolError::BadArray(why) => write!(f, "bad array from the worker: {why}"),
        }
    }
}

impl std::error::Error for ProtocolError {}

impl From<std::io::Error> for ProtocolError {
    fn from(e: std::io::Error) -> Self {
        ProtocolError::Io(e)
    }
}

/// Element type of an array on the wire.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dtype {
    F32,
    F64,
    U8,
}

impl Dtype {
    pub fn parse(s: &str) -> Option<Dtype> {
        match s {
            "<f4" => Some(Dtype::F32),
            "<f8" => Some(Dtype::F64),
            "|u1" | "<u1" | "u1" => Some(Dtype::U8),
            _ => None,
        }
    }

    pub fn item_size(self) -> usize {
        match self {
            Dtype::F32 => 4,
            Dtype::F64 => 8,
            Dtype::U8 => 1,
        }
    }
}

#[derive(Debug, Deserialize)]
struct ArrayDesc {
    name: String,
    dtype: String,
    shape: Vec<usize>,
    nbytes: usize,
}

/// The values of one array.
#[derive(Debug, Clone, PartialEq)]
pub enum ArrayData {
    F32(Vec<f32>),
    F64(Vec<f64>),
    U8(Vec<u8>),
}

impl ArrayData {
    /// The values as `f64` (exact for every dtype).
    pub fn to_f64(&self) -> Vec<f64> {
        match self {
            ArrayData::F32(v) => v.iter().map(|&x| x as f64).collect(),
            ArrayData::F64(v) => v.clone(),
            ArrayData::U8(v) => v.iter().map(|&x| x as f64).collect(),
        }
    }

    /// The values as `f32`; `None` for a float64 array (it would lose precision).
    pub fn into_f32(self) -> Option<Vec<f32>> {
        match self {
            ArrayData::F32(v) => Some(v),
            ArrayData::U8(v) => Some(v.into_iter().map(|x| x as f32).collect()),
            ArrayData::F64(_) => None,
        }
    }
}

/// One named array of a message.
#[derive(Debug, Clone, PartialEq)]
pub struct Array {
    pub name: String,
    pub shape: Vec<usize>,
    pub data: ArrayData,
}

/// One message of the worker: its header and its arrays.
#[derive(Debug, Clone)]
pub struct Message {
    pub header: Value,
    pub arrays: Vec<Array>,
}

impl Message {
    /// The `id` of the request this message answers (`None` for a protocol
    /// error that could not be tied to a request).
    pub fn id(&self) -> Option<u64> {
        self.header.get("id").and_then(Value::as_u64)
    }

    /// The message `type`.
    pub fn kind(&self) -> &str {
        self.header.get("type").and_then(Value::as_str).unwrap_or("")
    }

    /// Takes the array `name` out of the message.
    pub fn take(&mut self, name: &str) -> Result<Array, ProtocolError> {
        let pos = self
            .arrays
            .iter()
            .position(|a| a.name == name)
            .ok_or_else(|| ProtocolError::BadArray(format!("the {} message has no array '{name}'", self.kind())))?;
        Ok(self.arrays.remove(pos))
    }

    /// The header as a typed message.
    pub fn parse<T: for<'de> Deserialize<'de>>(&self) -> Result<T, ProtocolError> {
        T::deserialize(&self.header).map_err(|e| ProtocolError::BadHeader(format!("{} message: {e}", self.kind())))
    }
}

fn decode(dtype: Dtype, bytes: &[u8]) -> ArrayData {
    match dtype {
        Dtype::F32 => ArrayData::F32(
            bytes
                .chunks_exact(4)
                .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                .collect(),
        ),
        Dtype::F64 => ArrayData::F64(
            bytes
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))
                .collect(),
        ),
        Dtype::U8 => ArrayData::U8(bytes.to_vec()),
    }
}

/// Reads one message; `Ok(None)` at a clean end of stream (between messages).
pub fn read_message<R: BufRead>(reader: &mut R) -> Result<Option<Message>, ProtocolError> {
    let mut line = Vec::new();
    let n = reader.by_ref().take(MAX_LINE_BYTES as u64 + 1).read_until(b'\n', &mut line)?;
    if n == 0 {
        return Ok(None);
    }
    if line.last() != Some(&b'\n') {
        if line.len() > MAX_LINE_BYTES {
            return Err(ProtocolError::BadHeader(format!("a header line is longer than {MAX_LINE_BYTES} bytes")));
        }
        return Err(ProtocolError::Truncated("a header line".into()));
    }
    let header: Value = serde_json::from_slice(&line).map_err(|e| {
        let text = String::from_utf8_lossy(&line);
        let shown: String = text.trim_end().chars().take(200).collect();
        ProtocolError::BadHeader(format!("{e} in {shown:?}"))
    })?;
    if !header.is_object() {
        return Err(ProtocolError::BadHeader("a message must be a JSON object".into()));
    }
    let descs: Vec<ArrayDesc> = match header.get("arrays") {
        None | Some(Value::Null) => Vec::new(),
        Some(v) => Vec::<ArrayDesc>::deserialize(v).map_err(|e| ProtocolError::BadArray(e.to_string()))?,
    };
    let mut arrays = Vec::with_capacity(descs.len());
    for d in descs {
        let dtype = Dtype::parse(&d.dtype)
            .ok_or_else(|| ProtocolError::BadArray(format!("'{}' has the unknown dtype '{}'", d.name, d.dtype)))?;
        let count = d
            .shape
            .iter()
            .try_fold(1usize, |acc, &n| acc.checked_mul(n))
            .ok_or_else(|| ProtocolError::BadArray(format!("'{}' has an impossible shape {:?}", d.name, d.shape)))?;
        let expected = count.checked_mul(dtype.item_size());
        if expected != Some(d.nbytes) {
            return Err(ProtocolError::BadArray(format!(
                "'{}' declares {} bytes for shape {:?} of {}",
                d.name, d.nbytes, d.shape, d.dtype
            )));
        }
        if d.nbytes > MAX_ARRAY_BYTES {
            return Err(ProtocolError::BadArray(format!("'{}' is larger than {MAX_ARRAY_BYTES} bytes", d.name)));
        }
        let mut bytes = vec![0u8; d.nbytes];
        reader.read_exact(&mut bytes).map_err(|e| match e.kind() {
            std::io::ErrorKind::UnexpectedEof => ProtocolError::Truncated(format!("the array '{}'", d.name)),
            _ => ProtocolError::Io(e),
        })?;
        arrays.push(Array { name: d.name, shape: d.shape, data: decode(dtype, &bytes) });
    }
    Ok(Some(Message { header, arrays }))
}

/// One request line.
pub fn encode_request(request: &Value) -> Vec<u8> {
    let mut out = serde_json::to_vec(request).expect("a JSON value always serialises");
    out.push(b'\n');
    out
}

// --- Typed messages -----------------------------------------------------------

/// The kind of value an option takes, as the CLI's parser declares it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OptionKind {
    Flag,
    Int,
    Float,
    Choice,
    Str,
}

/// One CLI option of a view.
#[derive(Debug, Clone, Deserialize)]
pub struct OptionSchema {
    pub dest: String,
    pub flag: String,
    pub kind: OptionKind,
    #[serde(default)]
    pub default: Value,
    #[serde(default)]
    pub choices: Option<Vec<Value>>,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub help: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct ViewSchema {
    pub options: Vec<OptionSchema>,
    #[serde(default)]
    pub exclusive: Vec<Vec<String>>,
}

impl ViewSchema {
    pub fn option(&self, dest: &str) -> Option<&OptionSchema> {
        self.options.iter().find(|o| o.dest == dest)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct UnitInfo {
    pub name: String,
    pub quantity: String,
    #[serde(default)]
    pub unit: Option<String>,
    #[serde(default)]
    pub flag: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Hello {
    pub protocol: u32,
    pub audace_display: String,
    pub invisensing: String,
    pub python: String,
    #[serde(default)]
    pub numpy: String,
    pub views: BTreeMap<String, ViewSchema>,
    pub units: Vec<UnitInfo>,
    #[serde(default)]
    pub colormaps: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Availability {
    pub name: String,
    pub available: bool,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct Summary {
    pub name: String,
    pub format: String,
    pub mode: String,
    pub content_type: i64,
    pub content_type_name: String,
    pub quantity: Option<String>,
    pub card: Option<String>,
    pub format_version: i64,
    pub gauge_length_m: Option<f64>,
    pub num_lines: u64,
    pub line_rate_hz: f64,
    pub trig_frequency_hz: i64,
    pub trig_frequency: String,
    pub duration_s: f64,
    pub start_time: String,
    pub time_correction: Option<String>,
    pub sample_rate_hz: i64,
    pub positions: u64,
    pub step_m: f64,
    pub length_m: f64,
    pub compression: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FileDescription {
    pub path: String,
    pub summary: Summary,
    pub info_text: String,
    pub channels: Vec<String>,
    pub default_channel: String,
    pub default_view: String,
    pub views: Vec<Availability>,
    pub units: Vec<Availability>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Progress {
    pub stage: String,
    pub done: u64,
    #[serde(default)]
    pub total: Option<u64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WorkerError {
    pub kind: String,
    pub message: String,
}

/// A colormap as the worker sends it beside its lookup table.
#[derive(Debug, Clone, Deserialize)]
pub struct ColormapMeta {
    pub cmap: String,
    pub under: [u8; 4],
    pub over: [u8; 4],
    pub bad: [u8; 4],
}

#[derive(Debug, Clone, Deserialize)]
pub struct HeatmapMeta {
    pub title: String,
    pub label: String,
    pub t_extent: [f64; 2],
    pub d_extent: [f64; 2],
    #[serde(default)]
    pub vmin: Option<f64>,
    #[serde(default)]
    pub vmax: Option<f64>,
    #[serde(flatten)]
    pub colormap: ColormapMeta,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PanelMeta {
    pub title: String,
    pub label: String,
    #[serde(default)]
    pub vmin: Option<f64>,
    #[serde(default)]
    pub vmax: Option<f64>,
    #[serde(flatten)]
    pub colormap: ColormapMeta,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BandsMeta {
    pub title: String,
    pub t_extent: [f64; 2],
    pub d_extent: [f64; 2],
    pub panels: Vec<PanelMeta>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FftMeta {
    pub title: String,
    pub y_label: String,
    pub nyquist: f64,
    #[serde(default)]
    pub fmax: Option<f64>,
    pub curves: Vec<Option<String>>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TraceMeta {
    pub title: String,
    pub label: String,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct InspectMeta {
    pub title: String,
    pub y_label: String,
    #[serde(default)]
    pub ylim: [Option<f64>; 2],
    pub stats_text: String,
    pub fft_y_label: String,
    pub fft_log: bool,
    #[serde(default)]
    pub fmax: Option<f64>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FrameMeta {
    pub local: u64,
    pub line: u64,
    pub t: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ScopeMeta {
    pub session: u64,
    pub n_window: u64,
    pub trig_freq: f64,
    pub speed: f64,
    pub fps: f64,
    pub length_m: f64,
    pub y_label: String,
    pub title: String,
    pub ylim: [Option<f64>; 2],
    pub line_offset: u64,
    pub frame: FrameMeta,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct InfoMeta {
    pub text: String,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Cursor;

    fn message(header: Value, payload: &[u8]) -> Vec<u8> {
        let mut out = serde_json::to_vec(&header).unwrap();
        out.push(b'\n');
        out.extend_from_slice(payload);
        out
    }

    fn le_f32(v: &[f32]) -> Vec<u8> {
        v.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    fn le_f64(v: &[f64]) -> Vec<u8> {
        v.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    #[test]
    fn reads_a_header_without_arrays() {
        let bytes = message(json!({"id": 3, "type": "bye"}), b"");
        let mut r = Cursor::new(bytes);
        let m = read_message(&mut r).unwrap().unwrap();
        assert_eq!((m.id(), m.kind()), (Some(3), "bye"));
        assert!(m.arrays.is_empty());
        assert!(read_message(&mut r).unwrap().is_none(), "clean end of stream");
    }

    #[test]
    fn reads_arrays_in_order_with_their_dtypes() {
        let data = [1.5f32, -2.0, f32::NAN, 4.0, 5.0, 6.25];
        let axis = [0.1f64, 1e-300];
        let lut = [1u8, 2, 3, 4];
        let mut payload = le_f32(&data);
        payload.extend(le_f64(&axis));
        payload.extend(lut);
        let header = json!({"id": 7, "type": "heatmap", "arrays": [
            {"name": "data", "dtype": "<f4", "shape": [2, 3], "nbytes": 24},
            {"name": "axis", "dtype": "<f8", "shape": [2], "nbytes": 16},
            {"name": "lut", "dtype": "|u1", "shape": [1, 4], "nbytes": 4},
        ]});
        let mut bytes = message(header, &payload);
        // A second message right behind: the framing must stop exactly.
        bytes.extend(message(json!({"id": 8, "type": "bye"}), b""));
        let mut r = Cursor::new(bytes);
        let mut m = read_message(&mut r).unwrap().unwrap();
        let d = m.take("data").unwrap();
        assert_eq!(d.shape, vec![2, 3]);
        let ArrayData::F32(v) = &d.data else { panic!("f32 expected") };
        assert_eq!(v[0], 1.5);
        assert!(v[2].is_nan());
        assert_eq!(m.take("axis").unwrap().data, ArrayData::F64(axis.to_vec()));
        assert_eq!(m.take("lut").unwrap().data, ArrayData::U8(lut.to_vec()));
        assert_eq!(read_message(&mut r).unwrap().unwrap().id(), Some(8));
    }

    #[test]
    fn refuses_inconsistent_descriptors() {
        let header = json!({"id": 1, "type": "x", "arrays": [
            {"name": "a", "dtype": "<f4", "shape": [3], "nbytes": 8}]});
        let err = read_message(&mut Cursor::new(message(header, &[0; 8]))).unwrap_err();
        assert!(matches!(err, ProtocolError::BadArray(_)), "{err}");

        let header = json!({"id": 1, "type": "x", "arrays": [
            {"name": "a", "dtype": ">i8", "shape": [1], "nbytes": 8}]});
        let err = read_message(&mut Cursor::new(message(header, &[0; 8]))).unwrap_err();
        assert!(err.to_string().contains("unknown dtype"), "{err}");
    }

    #[test]
    fn a_short_payload_is_truncated_not_garbage() {
        let header = json!({"id": 1, "type": "x", "arrays": [
            {"name": "a", "dtype": "<f8", "shape": [4], "nbytes": 32}]});
        let err = read_message(&mut Cursor::new(message(header, &[0; 20]))).unwrap_err();
        assert!(matches!(err, ProtocolError::Truncated(_)), "{err}");
    }

    #[test]
    fn a_line_cut_short_is_truncated() {
        let err = read_message(&mut Cursor::new(b"{\"id\": 1".to_vec())).unwrap_err();
        assert!(matches!(err, ProtocolError::Truncated(_)), "{err}");
    }

    #[test]
    fn a_line_that_is_not_json_is_reported() {
        let err = read_message(&mut Cursor::new(b"Traceback (most recent call last):\n".to_vec())).unwrap_err();
        assert!(matches!(err, ProtocolError::BadHeader(_)));
        assert!(err.to_string().contains("Traceback"), "the line is quoted: {err}");
    }

    #[test]
    fn requests_are_one_line_each() {
        let bytes = encode_request(&json!({"id": 1, "cmd": "open", "path": "C:\\data\\a b.dat"}));
        assert_eq!(bytes.iter().filter(|&&b| b == b'\n').count(), 1);
        assert_eq!(*bytes.last().unwrap(), b'\n');
        let back: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(back["path"], "C:\\data\\a b.dat");
    }

    #[test]
    fn parses_typed_headers_with_nulls() {
        let m = Message {
            header: json!({"id": 2, "type": "heatmap", "title": "t", "label": "phase (rad)",
                "t_extent": [0.0, 0.5], "d_extent": [0.0, 51.2], "vmin": null, "vmax": 2.0,
                "cmap": "RdBu_r", "under": [5, 48, 97, 255], "over": [103, 0, 31, 255],
                "bad": [0, 0, 0, 0], "warnings": ["w"]}),
            arrays: vec![],
        };
        let h: HeatmapMeta = m.parse().unwrap();
        assert_eq!((h.vmin, h.vmax), (None, Some(2.0)));
        assert_eq!(h.colormap.cmap, "RdBu_r");
        assert_eq!(h.colormap.bad, [0, 0, 0, 0]);
    }

    #[test]
    fn parses_the_option_schema() {
        let v = json!({"options": [
            {"dest": "max_time_bins", "flag": "--max-time-bins", "kind": "int", "default": 2000,
             "choices": null, "required": false, "help": "Max time bins"},
            {"dest": "reduce", "flag": "--reduce", "kind": "choice", "default": "mean",
             "choices": ["mean", "rms"], "required": false, "help": ""}],
            "exclusive": [["position", "position_range"]]});
        let s = ViewSchema::deserialize(&v).unwrap();
        assert_eq!(s.option("max_time_bins").unwrap().kind, OptionKind::Int);
        assert_eq!(s.option("max_time_bins").unwrap().default, json!(2000));
        assert_eq!(s.option("reduce").unwrap().choices.as_ref().unwrap().len(), 2);
        assert_eq!(s.exclusive[0], vec!["position", "position_range"]);
    }
}
