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
