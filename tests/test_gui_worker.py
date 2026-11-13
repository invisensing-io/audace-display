"""The desktop viewer's worker (``python -m audace_display.gui_worker``).

What it serves must be what the CLI draws: each view is compared, array for
array and byte for byte, with what the CLI hands to the matplotlib builders
for the same options. The framing is checked on the real process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest
from invisensing import File, export_dat

from audace_display import cli, decimate, gui_worker, plotting
from audace_display.cli import main

needs_gauge = pytest.mark.skipif(
    not hasattr(File, "gauge_length_m"), reason="--nanostrain needs invisensing >= 1.6.0"
)
needs_rate = pytest.mark.skipif(
    not hasattr(File, "strain_rate_per_rad"), reason="--strain-rate needs invisensing >= 1.6.0"
)


# --- Helpers -------------------------------------------------------------------


class Session:
    """A worker in this process; ``request`` returns the messages it sent."""

    def __init__(self, is_cancelled=lambda _id: False):
        self.messages: list[tuple[dict, dict]] = []
        self.worker = gui_worker.Worker(self._send, is_cancelled)
        self._next = 0

    def _send(self, header, arrays=()):
        # Through the wire format, as the viewer reads it.
        self.messages.append(decode_message(gui_worker.encode_message(header, arrays)))

    def request(self, cmd, **fields):
        self._next += 1
        self.messages.clear()
        self.worker.handle({"id": self._next, "cmd": cmd, **fields})
        return list(self.messages)

    def result(self, cmd, **fields):
        """The one non-progress message of a request."""
        out = [m for m in self.request(cmd, **fields) if m[0]["type"] != "progress"]
        assert len(out) == 1, out
        return out[0]


def decode_message(data: bytes) -> tuple[dict, dict]:
    """The reference reader of one message (header line + arrays)."""
    line, _, rest = data.partition(b"\n")
    header = json.loads(line)
    arrays, offset = {}, 0
    for d in header.get("arrays", []):
        raw = rest[offset:offset + d["nbytes"]]
        arrays[d["name"]] = np.frombuffer(raw, dtype=np.dtype(d["dtype"])).reshape(d["shape"])
        offset += d["nbytes"]
    assert offset == len(rest), "trailing bytes after the arrays"
    return header, arrays


@pytest.fixture
def drawn(monkeypatch):
    """What the CLI hands to the matplotlib builders (nothing is drawn)."""
    seen = []

    def grab(name):
        def builder(*args, **kwargs):
            seen.append((name, args, kwargs))
        return builder

    for name in ("build_heatmap", "build_band_heatmaps", "build_trace", "build_fft",
                 "build_inspect", "animate_scope"):
        monkeypatch.setattr(plotting, name, grab(name))
    monkeypatch.setattr(plotting, "show_or_save", lambda *a, **k: None)
    return seen


def cli_draws(drawn, argv):
    drawn.clear()
    assert main(argv) == 0
    (call,) = drawn
    return call


@pytest.fixture
def gauge_phase(tmp_path):
    """A phase of data format v1 that records its gauge length."""
    rng = np.random.default_rng(11)
    data = (rng.standard_normal((400, 48)) * 2.0).astype(np.float32)
    path = tmp_path / "gauge.dat"
    export_dat(path, data, sample_rate=62_500_000, trig_frequency=1000,
               content_type=19, card="p21", gauge_length_m=9.6)
    return path


# --- hello / open ----------------------------------------------------------------


def test_hello_lists_the_cli_views_with_their_defaults():
    h, _ = Session().result("hello", protocol=gui_worker.PROTOCOL)
    assert h["type"] == "hello" and h["protocol"] == gui_worker.PROTOCOL
    assert set(h["views"]) == set(gui_worker.VIEWS)

    def opt(view, dest):
        (o,) = [o for o in h["views"][view]["options"] if o["dest"] == dest]
        return o

    assert opt("heatmap", "max_time_bins")["default"] == 2000
    assert opt("heatmap", "max_time_bins")["kind"] == "int"
    assert opt("bandheatmaps", "max_space_bins")["default"] == 1000
    assert opt("heatmap", "reduce")["choices"] == list(decimate.REDUCERS)
    assert opt("heatmap", "db")["kind"] == "flag"
    assert opt("trace", "position")["required"] is True
    assert ["position", "position_range"] in h["views"]["fft"]["exclusive"]
    # Files, the figure and the backend are not on-screen options.
    for view in h["views"].values():
        assert not {o["dest"] for o in view["options"]} & gui_worker.HIDDEN_OPTIONS
    assert "viridis" in h["colormaps"] and "RdBu_r" in h["colormaps"]
    assert h["units"][0]["name"] == "rad"


def test_hello_refuses_another_protocol():
    h, _ = Session().result("hello", protocol=gui_worker.PROTOCOL + 1)
    assert h["type"] == "error" and h["kind"] == "protocol"


def test_open_describes_a_phase(phase_dat):
    h, _ = Session().result("open", path=str(phase_dat))
    assert h["type"] == "file"
    s = h["summary"]
    assert (s["mode"], s["num_lines"], s["positions"]) == ("phase", 500, 64)
    assert s["line_rate_hz"] == 1000.0 and s["duration_s"] == 0.5
    assert h["channels"] == ["phase"] and h["default_channel"] == "phase"
    assert h["default_view"] == "heatmap"
    assert h["info_text"].startswith(f"File: {phase_dat}")


def test_open_a_raw_file_defaults_to_the_oscilloscope(raw_dat):
    h, _ = Session().result("open", path=str(raw_dat))
    assert h["default_view"] == "scope" and h["channels"] == ["raw"]


def test_open_lists_the_channels_of_an_iq_pair(iq_dat):
    h, _ = Session().result("open", path=str(iq_dat))
    assert h["channels"] == ["i", "q", "magnitude", "phase_wrapped"]
    assert h["default_channel"] == "magnitude"


@needs_gauge
def test_open_offers_strain_only_when_the_cli_does(phase_dat, gauge_phase):
    units = {u["name"]: u for u in Session().result("open", path=str(gauge_phase))[0]["units"]}
    assert units["nanostrain"]["available"] is True
    units = {u["name"]: u for u in Session().result("open", path=str(phase_dat))[0]["units"]}
    assert units["nanostrain"]["available"] is False
    assert "gauge length" in units["nanostrain"]["reason"]


# --- Every view is what the CLI draws -------------------------------------------


def test_heatmap_is_what_the_cli_draws(drawn, arctan_dat):
    _, args, kw = cli_draws(drawn, ["heatmap", str(arctan_dat), "--db",
                                    "--max-time-bins", "100", "--reduce", "rms"])
    h, a = Session().result("render", view="heatmap", path=str(arctan_dat),
                            options={"db": True, "max_time_bins": 100, "reduce": "rms"})
    assert h["type"] == "heatmap"
    assert a["data"].dtype == np.float32
    assert a["data"].tobytes() == np.ascontiguousarray(args[0], np.float32).tobytes()
    assert (h["vmin"], h["vmax"]) == (kw["vmin"], kw["vmax"])
    assert (h["label"], h["title"], h["cmap"]) == (kw["label"], kw["title"], kw["cmap"])
    assert tuple(h["t_extent"]) == kw["t_extent"] and tuple(h["d_extent"]) == kw["d_extent"]
    assert a["lut"].shape == (256, 4) and a["lut"].dtype == np.uint8


def test_a_zoomed_heatmap_is_the_cli_window(drawn, phase_dat):
    _, args, kw = cli_draws(drawn, ["heatmap", str(phase_dat), "--start-time", "0.1",
                                    "--duration", "0.2", "--start-distance", "10",
                                    "--end-distance", "30", "--variance", "log"])
    h, a = Session().result("render", view="heatmap", path=str(phase_dat), options={
        "start_time": 0.1, "duration": 0.2, "start_distance": 10.0, "end_distance": 30.0,
        "variance": "log"})
    assert a["data"].tobytes() == args[0].tobytes()
    assert tuple(h["t_extent"]) == kw["t_extent"] and tuple(h["d_extent"]) == kw["d_extent"]


def test_the_colormap_is_matplotlibs():
    import matplotlib

    lut, special = gui_worker.colormap_lut("RdBu_r")
    ref = matplotlib.colormaps["RdBu_r"](np.linspace(0, 1, 256))
    assert np.array_equal(lut, np.round(ref * 255).astype(np.uint8))
    assert special["bad"] == [0, 0, 0, 0]


def test_bandheatmaps_are_what_the_cli_draws(drawn, phase_dat):
    _, args, kw = cli_draws(drawn, ["bandheatmaps", str(phase_dat), "--f1", "50",
                                    "--f2", "100", "--variance", "log"])
    h, a = Session().result("render", view="bandheatmaps", path=str(phase_dat),
                            options={"f1": 50.0, "f2": 100.0, "variance": "log"})
    panels = args[0]
    assert len(h["panels"]) == len(panels) == 3
    for i, panel in enumerate(panels):
        assert a[f"panel{i}"].tobytes() == panel["data"].tobytes()
        for key in ("title", "label", "vmin", "vmax", "cmap"):
            assert h["panels"][i][key] == panel[key]
    assert h["title"] == kw["suptitle"]


def test_fft_is_what_the_cli_draws(drawn, iq_dat):
    _, args, kw = cli_draws(drawn, ["fft", str(iq_dat), "--position", "10,20", "--db",
                                    "--window", "blackman"])
    h, a = Session().result("render", view="fft", path=str(iq_dat),
                            options={"position": "10,20", "db": True, "window": "blackman"})
    freqs, curves = args
    assert a["freqs"].dtype == np.float64 and np.array_equal(a["freqs"], freqs)
    assert h["curves"] == [label for label, _ in curves]
    for i, (_, y) in enumerate(curves):
        assert a[f"curve{i}"].tobytes() == y.tobytes()
    assert (h["y_label"], h["nyquist"]) == (kw["y_label"], kw["nyquist"])


def test_trace_is_what_the_cli_draws(drawn, raw_dat):
    _, args, kw = cli_draws(drawn, ["trace", str(raw_dat), "--position", "5",
                                    "--max-points", "100"])
    h, a = Session().result("render", view="trace", path=str(raw_dat),
                            options={"position": 5.0, "max_points": 100})
    assert np.array_equal(a["times"], args[0])
    assert a["lo"].tobytes() == args[1].tobytes() and a["hi"].tobytes() == args[2].tobytes()
    assert h["label"] == kw["label"]


def test_inspect_is_what_the_cli_draws(drawn, phase_dat):
    _, args, kw = cli_draws(drawn, ["inspect", str(phase_dat), "--index", "7", "--fft-log"])
    h, a = Session().result("render", view="inspect", path=str(phase_dat),
                            options={"index": 7, "fft_log": True})
    for name, ref in zip(("times", "waveform", "freqs", "magnitude"), args):
        assert np.array_equal(a[name], ref)
    assert h["stats_text"] == kw["stats_text"] and h["fft_log"] is True
    assert tuple(h["ylim"]) == kw["ylim"]


def test_scope_frames_are_the_cli_lines(drawn, raw_dat):
    _, args, kw = cli_draws(drawn, ["scope", str(raw_dat), "--start-time", "0.1"])
    s = Session()
    h, first = s.result("render", view="scope", path=str(raw_dat), options={"start_time": 0.1})
    assert h["type"] == "scope"
    assert (h["n_window"], h["line_offset"]) == (args[1], kw["line_offset"])
    assert tuple(h["ylim"]) == kw["ylim"]
    with File(raw_dat) as f:
        # The CLI's getter, on a file of its own: line 0 then line 3 of the window.
        _, resolver, _, _ = cli.resolve_channel(None, f.mode, f)
        f.seek_lines(kw["line_offset"] + 3)
        ref = resolver(f, f.read_lines(1))[0]
    fh, frame = s.result("scope_frame", session=h["session"], local=3)
    xi, yv = decimate.peak_line(ref, kw["max_points"])
    assert np.array_equal(frame["y"], yv) and np.array_equal(frame["x"], xi * kw["d_step"])
    assert fh["line"] == kw["line_offset"] + 3
    s.worker.close()


@needs_gauge
def test_strain_is_served_as_the_cli_shows_it(drawn, gauge_phase):
    _, args, kw = cli_draws(drawn, ["heatmap", str(gauge_phase), "--nanostrain"])
    h, a = Session().result("render", view="heatmap", path=str(gauge_phase), unit="nanostrain")
    assert h["label"] == kw["label"] == "strain (nm/m)"
    assert a["data"].tobytes() == args[0].tobytes()


@needs_rate
def test_strain_rate_is_offered_beside_strain():
    h, _ = Session().result("hello", protocol=gui_worker.PROTOCOL)
    units = {u["name"]: u for u in h["units"]}
    assert units["strain_rate"]["unit"] == "nm/m/s"
    assert units["strain_rate"]["flag"] == "--strain-rate"
    # The CLI makes the two exclusive; the viewer sends one unit at a time.
    assert ["nanostrain", "strain_rate"] in h["views"]["heatmap"]["exclusive"]


@needs_rate
def test_open_offers_strain_rate_only_when_the_cli_does(phase_dat, gauge_phase):
    units = {u["name"]: u for u in Session().result("open", path=str(gauge_phase))[0]["units"]}
    assert units["strain_rate"]["available"] is True
    units = {u["name"]: u for u in Session().result("open", path=str(phase_dat))[0]["units"]}
    assert units["strain_rate"]["available"] is False
    assert "--strain-rate" in units["strain_rate"]["reason"]


@needs_rate
@pytest.mark.parametrize("view,argv,options", [
    ("heatmap", ["--max-time-bins", "50"], {"max_time_bins": 50}),
    ("fft", ["--position", "20"], {"position": "20"}),
    ("trace", ["--position", "20"], {"position": 20.0}),
])
def test_strain_rate_is_served_as_the_cli_shows_it(drawn, gauge_phase, view, argv, options):
    _, args, kw = cli_draws(drawn, [view, str(gauge_phase), "--strain-rate", *argv])
    h, a = Session().result("render", view=view, path=str(gauge_phase), unit="strain_rate",
                            options=options)
    assert h["type"] == view, h
    if view == "heatmap":
        assert a["data"].tobytes() == args[0].tobytes()  # NaN bins included
        assert h["label"] == kw["label"] and "nm/m/s" in h["label"]
    elif view == "fft":
        assert a["curve0"].tobytes() == args[1][0][1].tobytes()
    else:
        assert a["lo"].tobytes() == args[1].tobytes() and a["hi"].tobytes() == args[2].tobytes()


def test_info_is_the_cli_text(phase_dat, capsys):
    assert main(["info", str(phase_dat)]) == 0
    printed = capsys.readouterr().out
    h, _ = Session().result("render", view="info", path=str(phase_dat))
    assert h["text"] == printed


def test_cli_warnings_reach_the_viewer(phase_dat):
    h, _ = Session().result("render", view="bandheatmaps", path=str(phase_dat),
                            options={"f1": 600.0})
    assert any("Nyquist" in w for w in h["warnings"])


def test_progress_counts_the_pulses_read(phase_dat):
    msgs = Session().request("render", view="heatmap", path=str(phase_dat))
    progress = [m[0] for m in msgs if m[0]["type"] == "progress"]
    assert progress and progress[-1]["done"] == progress[-1]["total"] == 500


# --- Errors -----------------------------------------------------------------------


@pytest.mark.parametrize("fields,kind,text", [
    ({"view": "bandheatmaps", "options": {"f2": 200.0}}, "audace", "f1"),
    ({"view": "heatmap", "options": {"max_time_bins": 2.5}}, "options", "invalid int"),
    ({"view": "heatmap", "options": {"reduce": "median"}}, "options", "invalid choice"),
    ({"view": "heatmap", "options": {"no_such_option": 1}}, "options", "no option"),
    ({"view": "heatmap", "options": {"save": "x.png"}}, "options", "no option"),
    ({"view": "heatmap", "channel": "magnitude"}, "audace", "unavailable"),
    ({"view": "spectrogram"}, "options", "unknown view"),
    ({"view": "heatmap", "options": {"start_time": 99.0}}, "audace", "empty time range"),
])
def test_refusals_come_back_as_errors(phase_dat, fields, kind, text):
    h, _ = Session().result("render", path=str(phase_dat), **fields)
    assert h["type"] == "error" and h["kind"] == kind, h
    assert text in h["message"]


def test_a_missing_file_is_an_error():
    h, _ = Session().result("open", path="/no/such/file.dat")
    assert (h["type"], h["kind"]) == ("error", "file")


def test_strain_on_a_raw_file_is_the_cli_refusal(raw_dat):
    h, _ = Session().result("render", view="heatmap", path=str(raw_dat), unit="nanostrain")
    assert h["kind"] == "audace" and "not a phase" in h["message"]


@pytest.mark.parametrize("cmd,fields", [
    ("frobnicate", {}),
    ("render", {"view": "heatmap"}),               # no path
    ("scope_frame", {"session": 42, "local": 0}),  # no such session
])
def test_protocol_errors(cmd, fields):
    h, _ = Session().result(cmd, **fields)
    assert (h["type"], h["kind"]) == ("error", "protocol")


def test_a_cancelled_request_stops_between_chunks(phase_dat):
    checks = []

    def is_cancelled(_id):
        checks.append(_id)
        return len(checks) > 1  # not before it starts, at its first read

    h, _ = Session(is_cancelled).result("render", view="heatmap", path=str(phase_dat))
    assert (h["type"], h["kind"]) == ("error", "cancelled")


def test_non_finite_floats_travel_as_null():
    h, a = decode_message(gui_worker.encode_message(
        {"id": 1, "type": "x", "v": float("nan"), "w": [1.0, float("inf")]},
        (("a", np.array([np.nan, 1.0], np.float32)),)))
    assert h["v"] is None and h["w"] == [1.0, None]
    assert np.isnan(a["a"][0]) and a["a"].dtype == np.float32


# --- The real process -------------------------------------------------------------


def _read_message(stream) -> dict:
    line = stream.readline()
    assert line, "the worker closed its output"
    header = json.loads(line)
    for d in header.get("arrays", []):
        d["data"] = np.frombuffer(stream.read(d["nbytes"]), np.dtype(d["dtype"])).reshape(d["shape"])
    return header


def test_the_worker_process_speaks_only_the_protocol(phase_dat):
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "audace_display.gui_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        def ask(req):
            proc.stdin.write((json.dumps(req) + "\n").encode())
            proc.stdin.flush()
            while True:
                m = _read_message(proc.stdout)
                if m["type"] != "progress":
                    return m

        assert ask({"id": 1, "cmd": "hello", "protocol": gui_worker.PROTOCOL})["type"] == "hello"
        # `open` runs the CLI's info, which prints: none of it may reach stdout.
        assert ask({"id": 2, "cmd": "open", "path": str(phase_dat)})["type"] == "file"
        m = ask({"id": 3, "cmd": "render", "view": "heatmap", "path": str(phase_dat),
                 "options": {"max_time_bins": 50}})
        (data, lut) = m["arrays"]
        assert data["data"].shape == (50, 64) and lut["data"].shape == (256, 4)
        proc.stdin.write(b"this is not json\n")
        proc.stdin.flush()
        assert _read_message(proc.stdout)["kind"] == "protocol"
        assert ask({"id": 4, "cmd": "shutdown"})["type"] == "bye"
        assert proc.wait(timeout=30) == 0
        assert proc.stdout.read() == b""
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


# --- Message codes (the viewer renders them in its language) -----------------------


@pytest.fixture
def zero_trigger_phase(tmp_path):
    """A phase that records its gauge length but no trigger frequency."""
    path = tmp_path / "notrig.dat"
    export_dat(path, np.zeros((40, 16), np.float32), sample_rate=62_500_000, trig_frequency=0,
               content_type=19, card="p21", gauge_length_m=9.6)
    return path


#: Refusals the worker gets from the CLI on a real request: the view, its
#: options, the code, and parameters the code must carry (a subset).
CLI_REFUSALS = [
    ("bandheatmaps", {"f2": 200.0}, "bands_need_f1", {}),
    ("bandheatmaps", {"f1": 100.0, "f3": 300.0}, "bands_f3_needs_f2", {}),
    ("bandheatmaps", {"f0": -1.0, "f1": 100.0}, "bands_f0_negative", {}),
    ("bandheatmaps", {"f1": 200.0, "f2": 100.0}, "bands_not_increasing",
     {"edges": "0.0, 200.0, 100.0"}),
    ("bandheatmaps", {"f0": 600.0, "f1": 700.0}, "bands_none_below_nyquist", {}),
    ("bandheatmaps", {"f1": 100.0, "duration": 0.001}, "bands_too_few_pulses", {}),
    ("trace", {"position": 1e6}, "position_outside", {"position": "1000000.0"}),
    ("fft", {"position": "5000"}, "position_outside", {"position": "5000"}),
    ("fft", {"duration": 0.001}, "fft_too_few_pulses", {}),
    ("inspect", {"index": 10000}, "index_outside", {"index": "10000", "last": "63"}),
    ("inspect", {"duration": 0.001}, "inspect_too_few_pulses", {}),
    ("inspect", {"clip_percentile": 150.0}, "clip_percentile_range", {}),
    ("heatmap", {"start_time": 99.0}, "empty_time_range",
     {"p0": "500", "p1": "500", "pulses": "500"}),
    ("heatmap", {"cmap": "no_such_cmap"}, "colormap_unknown", {"name": "no_such_cmap"}),
    ("heatmap", {"channel": "bogus"}, "channel_unknown", {"channel": "bogus"}),
    ("heatmap", {"channel": "magnitude"}, "channel_unavailable",
     {"channel": "magnitude", "mode": "phase", "available": "phase"}),
    ("trace", {"position": 1.0, "max_points": 0}, "points_too_few", {}),
    ("heatmap", {"max_space_bins": 0}, "bins_too_few", {}),
    ("heatmap", {"max_time_bins": 2.5}, "option_invalid_value",
     {"flag": "--max-time-bins", "type": "int", "value": "2.5"}),
    ("heatmap", {"reduce": "median"}, "option_invalid_choice",
     {"flag": "--reduce", "value": "median"}),
    ("fft", {"position": "10", "position_range": "1:2"}, "option_not_allowed_with",
     {"flag": "--position-range", "other": "--position/-p"}),
    ("trace", {}, "option_required", {"flags": "--position/-p"}),
]


@pytest.mark.parametrize("view,options,code,params", CLI_REFUSALS)
def test_the_clis_refusals_carry_their_code(phase_dat, view, options, code, params):
    options = dict(options)
    channel = options.pop("channel", None)
    h, _ = Session().result("render", view=view, path=str(phase_dat), channel=channel,
                            options=options)
    assert h["type"] == "error" and h["code"] == code, h
    assert params.items() <= h["params"].items(), h
    # The message is the CLI's own text, its parameters taken from it.
    assert gui_worker.message_code(h["message"]) == (code, h["params"])


def test_a_refusal_with_its_code_is_the_clis_text(phase_dat, capsys):
    assert main(["bandheatmaps", str(phase_dat), "--f2", "200"]) == 1
    printed = capsys.readouterr().err
    h, _ = Session().result("render", view="bandheatmaps", path=str(phase_dat),
                            options={"f2": 200.0})
    assert f"ERROR: {h['message']}" in printed and h["code"] == "bands_need_f1"


@needs_gauge
def test_unit_refusals_carry_their_code(raw_dat, phase_dat, zero_trigger_phase):
    h, _ = Session().result("render", view="heatmap", path=str(raw_dat), unit="nanostrain")
    assert h["code"] == "unit_not_a_phase"
    assert {"option": "--nanostrain", "what": "strain", "unit": "nm/m", "file": "raw.dat",
            "mode": "raw"}.items() <= h["params"].items()
    h, _ = Session().result("render", view="heatmap", path=str(phase_dat), unit="nanostrain")
    assert (h["code"], h["params"]["file"]) == ("unit_no_gauge_length", "phase.dat")
    # And as the reason a unit is greyed out when the file is opened.
    units = {u["name"]: u for u in Session().result("open", path=str(raw_dat))[0]["units"]}
    assert units["nanostrain"]["code"] == "unit_not_a_phase"
    assert units["nanostrain"]["params"]["what"] == "strain"


@needs_rate
def test_a_strain_rate_without_trigger_frequency_carries_its_code(zero_trigger_phase):
    h, _ = Session().result("render", view="heatmap", path=str(zero_trigger_phase),
                            unit="strain_rate")
    assert (h["code"], h["params"]) == (
        "strain_rate_no_trigger", {"file": "notrig.dat", "hz": "0"})


def test_band_memory_refusal_carries_its_code(phase_dat, monkeypatch):
    from audace_display import reader
    real = reader.load_time_matrix
    monkeypatch.setattr(reader, "load_time_matrix",
                        lambda *a, **k: real(*a, **{**k, "max_cells": 10}))
    h, _ = Session().result("render", view="bandheatmaps", path=str(phase_dat),
                            options={"f1": 100.0})
    assert h["code"] == "bands_too_much_memory", h
    assert h["params"]["pulses"] == "500"


@pytest.mark.parametrize("view,options,patch,code,params", [
    ("bandheatmaps", {"f1": 600.0}, None, "warn_band_edge_clamped",
     {"edge": "600", "nyquist": "500"}),
    ("fft", {}, ("_FFT_MAX_POSITIONS", 8), "warn_fft_subsampled",
     {"dropped": "64", "kept": "8", "step": "8"}),
    ("fft", {"position": "1,2"}, ("_FFT_MAX_CELLS", 10), "warn_fft_memory",
     {"pulses": "500", "positions": "2"}),
])
def test_the_clis_warnings_carry_their_code(phase_dat, monkeypatch, view, options, patch,
                                            code, params):
    if patch is not None:
        monkeypatch.setattr(cli, *patch)
    h, _ = Session().result("render", view=view, path=str(phase_dat), options=options)
    assert h["type"] == view and len(h["warning_codes"]) == len(h["warnings"])
    found = [c for c in h["warning_codes"] if c is not None and c["code"] == code]
    assert found and params.items() <= found[0]["params"].items(), h["warning_codes"]


def test_every_result_has_its_warning_codes(phase_dat, raw_dat):
    s = Session()
    for view, path, options in [("info", phase_dat, {}), ("heatmap", phase_dat, {}),
                                ("trace", phase_dat, {"position": 2.0}),
                                ("inspect", phase_dat, {}), ("scope", raw_dat, {})]:
        h, _ = s.result("render", view=view, path=str(path), options=options)
        assert h["type"] == view and h["warning_codes"] == [None] * len(h["warnings"])
    h, _ = s.result("open", path=str(phase_dat))
    assert len(h["warning_codes"]) == len(h["warnings"])


#: CLI messages no request reaches on the test files (they need another
#: invisensing, a broken file or a huge one): their patterns are checked on
#: the CLI's wording.
CLI_LITERALS = {
    "trace_no_data": ("no data read for the trace.", {}),
    "fft_too_few_samples": ("not enough time samples for an FFT.", {}),
    "scope_needs_invisensing": (
        "oscilloscope mode requires invisensing >= 1.1.0 (O(1) per-line seek).", {}),
    "unit_needs_invisensing": (
        "--strain-rate needs invisensing >= 1.6.0 (pip install -U invisensing).",
        {"option": "--strain-rate"}),
    "unit_refused": ("--nanostrain: bad phase_unit", {"option": "--nanostrain",
                                                     "detail": "bad phase_unit"}),
    "empty_distance_range": (
        "empty spatial slice after --start-distance/--end-distance (d0=5, d1=5, positions=0).",
        {"d0": "5", "d1": "5", "positions": "0"}),
    "no_data": ("no data read.", {}),
    "empty_position_spec": ("empty position spec: ''.", {"spec": ""}),
    "window_unknown": ("unknown window 'kaiser'. Known: rect, hann, hamming, blackman.",
                       {"window": "kaiser", "known": "rect, hann, hamming, blackman"}),
    "warn_bands_memory": (
        "bandheatmaps: ~1.2 GB to load (1,500,000 pulses x 200 bins). Use --duration / "
        "--max-pulses / --max-space-bins if memory is limited.",
        {"gb": "1.2", "pulses": "1,500,000", "bins": "200"}),
}


@pytest.mark.parametrize("code", sorted(CLI_LITERALS))
def test_the_patterns_of_unreachable_cli_messages(code):
    text, params = CLI_LITERALS[code]
    assert gui_worker.message_code(text) == (code, params)


def test_a_message_no_pattern_knows_has_no_code():
    assert gui_worker.message_code("something else entirely.") is None
    assert gui_worker.message_code("no data read. And more.") is None
    assert gui_worker._warning_codes(["odd", "no data read."]) == [
        None, {"code": "no_data", "params": {}}]


@pytest.mark.parametrize("cmd,fields,kind,code,params", [
    ("render", {"view": "heatmap", "options": {"no_such_option": 1}}, "options",
     "option_unknown", {"view": "heatmap", "option": "no_such_option"}),
    ("render", {"view": "spectrogram"}, "options", "view_unknown", {"view": "spectrogram"}),
    ("render", {"view": "heatmap", "options": {"db": "yes"}}, "options", "option_not_bool",
     {"flag": "--db", "value": "'yes'"}),
    ("render", {"view": "heatmap", "options": {"max_time_bins": True}}, "options",
     "option_takes_value", {"flag": "--max-time-bins"}),
    ("render", {"view": "heatmap", "options": {"max_time_bins": [1]}}, "options",
     "option_unsupported_value", {"flag": "--max-time-bins", "value": "[1]"}),
    ("render", {"view": "heatmap", "unit": "furlongs"}, "options", "unit_not_offered",
     {"unit": "furlongs", "view": "heatmap"}),
    ("render", {"view": "heatmap", "options": [1]}, "options", "options_not_object", {}),
    ("render", {"view": "fft", "options": {"position": "abc"}}, "internal", "internal",
     {"exception": "ValueError"}),
    ("frobnicate", {}, "protocol", "unknown_command", {"command": "'frobnicate'"}),
    ("render", {"view": "heatmap", "path": None}, "protocol", "request_field",
     {"name": "path", "type": "str"}),
    ("scope_frame", {"session": 42, "local": 0}, "protocol", "scope_session_gone", {}),
])
def test_the_workers_own_errors_carry_their_code(phase_dat, cmd, fields, kind, code, params):
    fields = {"path": str(phase_dat), **fields}
    if fields["path"] is None:
        del fields["path"]
    h, _ = Session().result(cmd, **fields)
    assert (h["type"], h["kind"], h["code"]) == ("error", kind, code), h
    assert params.items() <= h["params"].items(), h


def test_own_codes_of_hello_open_and_cancel(phase_dat):
    h, _ = Session().result("hello", protocol=gui_worker.PROTOCOL + 1)
    assert h["code"] == "protocol_mismatch"
    assert h["params"]["client"] == str(gui_worker.PROTOCOL + 1)
    h, _ = Session().result("open", path="/no/such/file.dat")
    assert (h["code"], h["params"]["path"]) == ("file_not_found", "/no/such/file.dat")
    assert h["message"].startswith("file not found: ")
    h, _ = Session(lambda _id: True).result("render", view="heatmap", path=str(phase_dat))
    assert (h["kind"], h["code"], h["message"]) == ("cancelled", "cancelled", "cancelled.")


def test_message_codes_lists_every_code_with_its_parameters():
    codes = gui_worker.message_codes()
    assert codes["position_outside"] == ["max", "position"]
    assert codes["file_not_found"] == ["detail", "path"]
    # Each own message formats from its parameters alone.
    for code, template in gui_worker._OWN_MESSAGES.items():
        assert template.format(**{p: "x" for p in codes[code]})
    # Every code is exercised by a test of this file.
    tested = ({c for _v, _o, c, _p in CLI_REFUSALS} | set(CLI_LITERALS)
              | {"unit_not_a_phase", "unit_no_gauge_length", "strain_rate_no_trigger",
                 "bands_too_much_memory", "warn_band_edge_clamped", "warn_fft_subsampled",
                 "warn_fft_memory", "option_unknown", "view_unknown", "option_not_bool",
                 "option_takes_value", "option_unsupported_value", "unit_not_offered",
                 "options_not_object", "internal", "unknown_command", "request_field",
                 "scope_session_gone", "protocol_mismatch", "file_not_found", "cancelled",
                 "scope_unavailable"})
    assert tested == set(codes)


def test_an_unavailable_oscilloscope_carries_its_code(phase_dat, monkeypatch):
    import invisensing
    monkeypatch.delattr(invisensing.File, "seek_lines", raising=False)
    h, _ = Session().result("open", path=str(phase_dat))
    (scope,) = [v for v in h["views"] if v["name"] == "scope"]
    if scope["available"]:  # pragma: no cover -- a File that keeps seek_lines elsewhere
        pytest.skip("this invisensing cannot be made to lack seek_lines")
    assert (scope["code"], scope["reason"]) == (
        "scope_unavailable", "the oscilloscope needs invisensing >= 1.1.0.")
