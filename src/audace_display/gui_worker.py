"""Backend of the desktop viewer (``gui/``): audace-display's own views, served
over a line-delimited JSON protocol on stdin / stdout.

Run as ``python -m audace_display.gui_worker``. The viewer spawns it with the
Python interpreter the user picked; nothing is meant to be typed by hand.

What the viewer shows is what the CLI computes
----------------------------------------------
A request names a view (``info``, ``heatmap``, ``bandheatmaps``, ``fft``,
``trace``, ``scope``, ``inspect``), a file and the CLI options of that view.
The worker turns it into the command line the CLI would parse
(:func:`audace_display.cli.build_parser`: same defaults, same validation),
opens the file as the CLI opens it (``--nanostrain`` refusals included) and
runs the CLI's own ``do_<view>`` function with the matplotlib figure builders
of :mod:`audace_display.plotting` swapped for recorders. What the CLI hands to
matplotlib -- the decimated matrix and its extents, the colour limits, the
colormap, the label and title, the spectra, traces and oscilloscope lines --
is what the viewer receives, bit for bit. An option the CLI gains (such as
``--strain-rate``) is listed and served without a change here.

Protocol, version :data:`PROTOCOL`
----------------------------------
Requests (viewer -> worker): one UTF-8 JSON object per line, with an integer
``id`` and a ``cmd``:

``{"id": 1, "cmd": "hello", "protocol": 1}``
    Versions, the views and the options of each (from the CLI's parser:
    flag, kind, default, choices, help, mutually exclusive groups), the
    units the CLI offers, the colormap names.
``{"id": 2, "cmd": "open", "path": "..."}``
    What the file is: the CLI's ``info`` text, a structured summary (mode,
    content type, gauge length, actual trigger frequency, time correction,
    pulses, positions...), its channels, its default view, and for each unit
    whether this file can be shown in it (with the CLI's refusal otherwise).
``{"id": 3, "cmd": "render", "view": "heatmap", "path": "...",``
``"channel": null, "unit": "rad", "options": {"db": true, "max_time_bins": 2000}}``
    One view. ``options`` maps a CLI option's ``dest`` to its value; an
    absent or ``null`` option takes the CLI's default.
``{"id": 4, "cmd": "scope_frame", "session": 3, "local": 120}``
    One oscilloscope line of the session the ``scope`` render ``3`` opened.
``{"cmd": "cancel", "target": 3}``
    Stops request ``3`` (it then answers with an error of kind
    ``cancelled``). Takes effect between two chunks of the read.
``{"id": 5, "cmd": "shutdown"}``
    Answers ``bye`` and exits. End of input exits too.

Messages (worker -> viewer): one UTF-8 JSON object per line, with the ``id``
of the request and a ``type``. A message carrying arrays lists them in
``arrays``: ``[{"name": "data", "dtype": "<f4", "shape": [500, 64],
"nbytes": 128000}, ...]``; exactly the sum of their ``nbytes`` follows the
line's ``\\n``, the arrays one after the other, C order, little-endian
(``<f4`` float32, ``<f8`` float64, ``|u1`` uint8). Types:

- ``hello``, ``file``, ``info``, ``heatmap``, ``bandheatmaps``, ``fft``,
  ``trace``, ``inspect``, ``scope``, ``scope_frame``, ``bye``: results;
- ``progress``: ``{"stage": "reading", "done": 1200, "total": 5000}``, any
  number of them before the result of a long request;
- ``error``: ``{"kind": ..., "message": ...}``; ``kind`` is ``audace`` (the
  CLI's own refusal, the text it prints after ``ERROR:``), ``options`` (an
  option the CLI's parser refuses), ``file``, ``cancelled``, ``protocol`` or
  ``internal`` (a bug: the traceback goes to stderr).

Every result carries ``warnings``: the CLI's ``WARNING:`` lines and Python
warnings raised while it was computed. Non-finite floats travel as ``null``.
The worker writes nothing else to stdout: stray prints go to stderr.
"""
from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import math
import os
import queue
import sys
import threading
import time
import traceback
import warnings
from typing import Any, Callable, Optional

# Never open a window: the figure builders are swapped for recorders, and
# matplotlib must not pick an interactive backend behind them.
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np  # noqa: E402

from . import cli, decimate, plotting, reader  # noqa: E402
from ._errors import AudaceDisplayError  # noqa: E402
from .channels import CHANNELS, DEFAULT_CHANNEL  # noqa: E402

#: Version of the protocol. Bumped on any change a viewer built against the
#: previous version would misread.
PROTOCOL = 1

#: The CLI subcommands the viewer shows, in its order.
VIEWS = ("info", "heatmap", "bandheatmaps", "fft", "trace", "scope", "inspect")

#: Options of the CLI that do not apply to an on-screen view: files, the
#: matplotlib figure, the backend, the demod plugin script.
HIDDEN_OPTIONS = frozenset(
    {"help", "path", "save", "dpi", "title", "backend", "aspect", "script"}
)

#: Units a phase can be shown in, beyond the radians it is stored in:
#: ``(CLI option dest, quantity, unit)``. Offered only when the installed
#: CLI has the option.
UNITS = (
    ("nanostrain", "strain", "nm/m"),
    ("strain_rate", "strain rate", "nm/m/s"),
)

#: Views whose cost is the read of the time window (progress is reported).
_WINDOWED_VIEWS = frozenset({"heatmap", "bandheatmaps", "fft", "trace", "inspect"})

#: The figure builders the CLI calls; each is recorded instead of drawn.
_BUILDERS = (
    "build_heatmap", "build_band_heatmaps", "build_trace", "build_fft",
    "build_line", "build_inspect", "animate_scope",
)

#: Seconds between two progress messages of one request.
_PROGRESS_PERIOD_S = 0.1


class _Cancelled(Exception):
    """The viewer cancelled the request being computed."""


class _OptionsError(Exception):
    """The CLI's parser refused the options of a request."""


class _ProtocolError(Exception):
    """A request the protocol does not allow (a missing field, a wrong type)."""


def _field(req: dict, name: str, kind: type):
    value = req.get(name)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise _ProtocolError(f"the request needs '{name}' ({kind.__name__}).")
    return value


# --- Wire format -------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """``value`` as JSON can carry it: non-finite floats as ``None``, numpy
    scalars as Python numbers, tuples as lists."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return v if math.isfinite(v) else None
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _wire_array(arr: Any) -> np.ndarray:
    """``arr`` as it travels: float32 and float64 keep their precision,
    uint8 stays bytes, anything else becomes float64; little-endian, C order."""
    a = np.asarray(arr)
    if a.dtype == np.uint8:
        return np.ascontiguousarray(a)
    if a.dtype == np.float32:
        return np.ascontiguousarray(a, dtype="<f4")
    return np.ascontiguousarray(a, dtype="<f8")


def encode_message(header: dict, arrays: tuple = ()) -> bytes:
    """One message: its JSON line, then the bytes of its arrays.

    ``arrays`` is a sequence of ``(name, array)``; their descriptors are
    added to the header as ``arrays``.
    """
    wired = [(name, _wire_array(a)) for name, a in arrays]
    head = dict(header)
    if wired:
        head["arrays"] = [
            {"name": name, "dtype": a.dtype.str, "shape": list(a.shape), "nbytes": int(a.nbytes)}
            for name, a in wired
        ]
    line = json.dumps(_jsonable(head), allow_nan=False, separators=(",", ":")) + "\n"
    return line.encode("utf-8") + b"".join(a.tobytes(order="C") for _, a in wired)


class _Channel:
    """Writes messages to the protocol stream, one whole message at a time."""

    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, header: dict, arrays: tuple = ()) -> None:
        data = encode_message(header, arrays)
        with self._lock:
            self._stream.write(data)
            self._stream.flush()


# --- Requests ----------------------------------------------------------------


def _read_lines(fd: int):
    """The lines of ``fd``, read with ``os.read``: no buffered-reader lock is
    held by the reading thread when the interpreter exits."""
    pending = b""
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            if pending:
                yield pending
            return
        pending += chunk
        *lines, pending = pending.split(b"\n")
        yield from lines


class _Inbox:
    """Reads requests on a thread of its own, so a ``cancel`` reaches the
    request being computed."""

    def __init__(self, fd: int):
        self._queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._cancelled: set = set()
        self._lock = threading.Lock()
        thread = threading.Thread(target=self._run, args=(fd,), daemon=True,
                                  name="gui-worker-stdin")
        thread.start()

    def _run(self, fd: int) -> None:
        for raw in _read_lines(fd):
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                self._queue.put(("bad", f"a request line is not JSON: {e}"))
                continue
            if not isinstance(req, dict):
                self._queue.put(("bad", "a request must be a JSON object"))
                continue
            if req.get("cmd") == "cancel":
                with self._lock:
                    self._cancelled.add(req.get("target"))
                continue
            self._queue.put(("request", req))
        self._queue.put(("eof", None))

    def get(self) -> tuple[str, Any]:
        return self._queue.get()

    def is_cancelled(self, request_id) -> bool:
        with self._lock:
            return request_id in self._cancelled

    def forget(self, request_id) -> None:
        with self._lock:
            self._cancelled.discard(request_id)


class _Job:
    """One request being computed: its progress messages and its cancel flag."""

    def __init__(self, request_id, send: Callable, is_cancelled: Callable[[], bool]):
        self.id = request_id
        self._send = send
        self._is_cancelled = is_cancelled
        self._last = 0.0

    def check(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    def progress(self, stage: str, done: int, total: Optional[int]) -> None:
        now = time.monotonic()
        final = total is not None and done >= total
        if not final and now - self._last < _PROGRESS_PERIOD_S:
            return
        self._last = now
        self._send({"id": self.id, "type": "progress", "stage": stage,
                    "done": int(done), "total": None if total is None else int(total)})


# --- The CLI, driven ---------------------------------------------------------


def _subparsers(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise RuntimeError("the CLI parser has no subcommands")


def _long_flag(action: argparse.Action) -> str:
    longs = [s for s in action.option_strings if s.startswith("--")]
    return (longs or action.option_strings)[0]


def _option_schema(action: argparse.Action) -> dict:
    if isinstance(action, argparse._StoreTrueAction):
        kind = "flag"
    elif action.choices is not None:
        kind = "choice"
    elif action.type is int:
        kind = "int"
    elif action.type is float:
        kind = "float"
    else:
        kind = "str"
    return {
        "dest": action.dest,
        "flag": _long_flag(action),
        "kind": kind,
        "default": action.default,
        "choices": list(action.choices) if action.choices is not None else None,
        "required": bool(action.required),
        "help": action.help or "",
    }


def view_schema(parser: Optional[argparse.ArgumentParser] = None) -> dict:
    """The options of each view, from the CLI's parser."""
    subs = _subparsers(parser or cli.build_parser())
    out = {}
    for view in VIEWS:
        sub = subs.get(view)
        if sub is None:
            continue
        options = [
            _option_schema(a) for a in sub._actions
            if a.option_strings and a.dest not in HIDDEN_OPTIONS
        ]
        exclusive = [
            [a.dest for a in group._group_actions if a.dest not in HIDDEN_OPTIONS]
            for group in sub._mutually_exclusive_groups
        ]
        out[view] = {"options": options, "exclusive": [g for g in exclusive if len(g) > 1]}
    return out


def _format_value(action: argparse.Action, value: Any) -> str:
    if isinstance(value, bool):
        raise _OptionsError(f"{_long_flag(action)} takes a value, not true/false.")
    if isinstance(value, float):
        return repr(value)  # shortest text that parses back to the same float
    if isinstance(value, (int, str)):
        return str(value)
    raise _OptionsError(f"{_long_flag(action)}: unsupported value {value!r}.")


def build_argv(view: str, path: str, *, channel: Optional[str] = None,
               unit: str = "rad", options: Optional[dict] = None,
               parser: Optional[argparse.ArgumentParser] = None) -> list[str]:
    """The CLI command line of a request (without the program name)."""
    subs = _subparsers(parser or cli.build_parser())
    if view not in VIEWS or view not in subs:
        raise _OptionsError(f"unknown view '{view}'. Known: {', '.join(VIEWS)}.")
    actions = {a.dest: a for a in subs[view]._actions if a.option_strings}
    argv = [view, str(path)]
    opts = dict(options or {})
    if channel is not None:
        opts["channel"] = channel
    if unit not in (None, "rad"):
        known = {dest for dest, _, _ in UNITS}
        if unit not in known or unit not in actions:
            raise _OptionsError(f"the installed CLI cannot show '{unit}' for the {view} view.")
        opts[unit] = True
    for dest, value in opts.items():
        action = actions.get(dest)
        if action is None or dest in HIDDEN_OPTIONS:
            raise _OptionsError(f"the {view} view has no option '{dest}'.")
        if value is None:
            continue
        if isinstance(action, argparse._StoreTrueAction):
            if not isinstance(value, bool):
                raise _OptionsError(f"{_long_flag(action)} is true or false, got {value!r}.")
            if value:
                argv.append(_long_flag(action))
            continue
        argv.append(f"{_long_flag(action)}={_format_value(action, value)}")
    return argv


def parse_request_args(view: str, path: str, *, channel: Optional[str] = None,
                       unit: str = "rad", options: Optional[dict] = None) -> argparse.Namespace:
    """The CLI's ``args`` for a request, parsed by the CLI's own parser."""
    parser = cli.build_parser()
    argv = build_argv(view, path, channel=channel, unit=unit, options=options, parser=parser)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            args = parser.parse_args(argv)
    except SystemExit:
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        message = lines[-1] if lines else "invalid options"
        # "audace-display heatmap: error: argument --max-time-bins: ..." -> after "error: "
        raise _OptionsError(message.split("error: ", 1)[-1]) from None
    args.plugin_args = []
    return args


#: The signatures of the real figure builders, taken once at import: a
#: recorded call is bound to them, whatever stands in :mod:`plotting` later.
_SIGNATURES = {name: inspect.signature(getattr(plotting, name)) for name in _BUILDERS}


class _Recorder:
    """Stands in for :mod:`audace_display.plotting` while a view is computed:
    each figure builder records the arguments it was called with."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def _builder(self, name: str) -> Callable:
        signature = _SIGNATURES[name]

        def record(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            self.calls.append((name, dict(bound.arguments)))
            return None
        return record

    def only(self, name: str) -> dict:
        found = [a for n, a in self.calls if n == name]
        if len(found) != 1:
            drawn = ", ".join(n for n, _ in self.calls) or "nothing"
            raise RuntimeError(f"the view drew {drawn}, expected one {name}")
        return found[0]


@contextlib.contextmanager
def _recording():
    rec = _Recorder()
    saved = {name: getattr(plotting, name) for name in (*_BUILDERS, "show_or_save")}
    try:
        for name in _BUILDERS:
            setattr(plotting, name, rec._builder(name))
        plotting.show_or_save = lambda *a, **k: None
        yield rec
    finally:
        for name, fn in saved.items():
            setattr(plotting, name, fn)


class _Tee(io.TextIOBase):
    """stderr while a view is computed: passed through, and kept."""

    def __init__(self, target):
        self._target = target
        self.text = io.StringIO()

    def write(self, s: str) -> int:
        self.text.write(s)
        return self._target.write(s)

    def flush(self) -> None:
        self._target.flush()


@contextlib.contextmanager
def _captured():
    """Collects what the CLI prints (``info``), its ``WARNING:`` lines and the
    Python warnings raised meanwhile."""
    out = io.StringIO()
    tee = _Tee(sys.stderr)
    result = {"stdout": "", "warnings": []}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(tee):
                yield result
        finally:
            result["stdout"] = out.getvalue()
            lines = [ln[len("WARNING: "):] for ln in tee.text.getvalue().splitlines()
                     if ln.startswith("WARNING: ")]
            lines += [str(w.message) for w in caught]
            result["warnings"] = lines


@contextlib.contextmanager
def _instrumented(f, job: _Job, total: Optional[int]):
    """Counts the pulses the view reads (progress) and stops it between two
    chunks when the viewer cancels."""
    original = f.read_lines
    done = [0]

    def read_lines(*args, **kwargs):
        job.check()
        block = original(*args, **kwargs)
        done[0] += int(np.shape(block)[0])
        job.progress("reading", done[0], total)
        return block

    f.read_lines = read_lines
    try:
        yield
    finally:
        with contextlib.suppress(AttributeError):
            del f.read_lines


def colormap_lut(name: str) -> tuple[np.ndarray, dict]:
    """``(lut (N, 4) uint8, {"under", "over", "bad"})`` of a matplotlib
    colormap: the colours the CLI's figure would use."""
    import matplotlib

    try:
        cmap = matplotlib.colormaps[name]
    except KeyError:
        raise AudaceDisplayError(f"unknown colormap '{name}'.") from None
    rgba = cmap(np.linspace(0.0, 1.0, cmap.N))
    lut = np.clip(np.round(np.asarray(rgba) * 255.0), 0, 255).astype(np.uint8)

    def byte(c) -> list[int]:
        return [int(round(float(v) * 255.0)) for v in c]

    return lut, {"under": byte(cmap.get_under()), "over": byte(cmap.get_over()),
                 "bad": byte(cmap.get_bad())}


def colormap_names() -> list[str]:
    import matplotlib
    return sorted(matplotlib.colormaps, key=str.lower)


# --- The worker --------------------------------------------------------------


class _ScopeSession:
    """An open oscilloscope: the file stays open and the CLI's line getter
    serves each frame."""

    def __init__(self, sid, f, params: dict):
        self.id = sid
        self.file = f
        self.params = params

    def frame(self, local: int) -> tuple[dict, tuple]:
        p = self.params
        span = max(int(p["n_window"]), 1)
        local = int(local) % span
        # The frame of plotting.animate_scope's update(), as the CLI draws it.
        y = np.asarray(p["get_y"](local))
        xi, yv = decimate.peak_line(y, p["max_points"])
        line = int(p["line_offset"]) + local
        head = {"session": self.id, "local": local, "line": line,
                "t": line / float(p["trig_freq"])}
        return head, (("x", xi * p["d_step"]), ("y", yv))

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.file.close()


class Worker:
    """Serves requests; ``send(header, arrays)`` writes one message."""

    def __init__(self, send: Callable[..., None],
                 is_cancelled: Callable[[Any], bool] = lambda _id: False):
        self._send = send
        self._is_cancelled = is_cancelled
        self._scope: Optional[_ScopeSession] = None

    def close(self) -> None:
        if self._scope is not None:
            self._scope.close()
            self._scope = None

    # One request -> exactly one result or error message.
    def handle(self, req: dict) -> bool:
        """Serves ``req``; returns ``False`` once the worker must stop."""
        rid = req.get("id")
        cmd = req.get("cmd")
        job = _Job(rid, self._send, lambda: self._is_cancelled(rid))
        try:
            job.check()
            if cmd == "hello":
                self._hello(rid, req)
            elif cmd == "open":
                self._open_file(rid, req, job)
            elif cmd == "render":
                self._render(rid, req, job)
            elif cmd == "scope_frame":
                self._scope_frame(rid, req)
            elif cmd == "shutdown":
                self._send({"id": rid, "type": "bye"})
                return False
            else:
                self._error(rid, "protocol", f"unknown command {cmd!r}.")
        except _Cancelled:
            self._error(rid, "cancelled", "cancelled.")
        except _OptionsError as e:
            self._error(rid, "options", str(e))
        except AudaceDisplayError as e:
            self._error(rid, "audace", str(e))
        except FileNotFoundError as e:
            self._error(rid, "file", f"file not found: {e}")
        except _ProtocolError as e:
            self._error(rid, "protocol", str(e))
        except Exception as e:  # noqa: BLE001 -- reported, never fatal
            self._internal(rid, e)
        return True

    def _error(self, rid, kind: str, message: str) -> None:
        self._send({"id": rid, "type": "error", "kind": kind, "message": message})

    def _internal(self, rid, e: BaseException) -> None:
        traceback.print_exc(file=sys.stderr)
        self._error(rid, "internal", f"unexpected {e.__class__.__name__}: {e}")

    # -- hello --------------------------------------------------------------

    def _hello(self, rid, req: dict) -> None:
        import platform

        import invisensing

        from . import __version__

        client = req.get("protocol")
        if client is not None and client != PROTOCOL:
            self._error(rid, "protocol",
                        f"the viewer speaks protocol {client}, this audace-display "
                        f"{__version__} speaks protocol {PROTOCOL}: install the "
                        "audace-display that matches the viewer.")
            return
        schema = view_schema()
        dests = {o["dest"] for v in schema.values() for o in v["options"]}
        units = [{"name": "rad", "quantity": "as stored", "unit": None, "flag": None}]
        units += [
            {"name": dest, "quantity": quantity, "unit": unit,
             "flag": "--" + dest.replace("_", "-")}
            for dest, quantity, unit in UNITS if dest in dests
        ]
        self._send({
            "id": rid, "type": "hello", "protocol": PROTOCOL,
            "audace_display": __version__,
            "invisensing": getattr(invisensing, "__version__", "?"),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "views": schema,
            "units": units,
            "colormaps": colormap_names(),
        })

    # -- open -----------------------------------------------------------------

    def _open_file(self, rid, req: dict, job: _Job) -> None:
        from invisensing import File, Mode

        path = _field(req, "path", str)
        with File(path) as f:
            mode = f.mode
            content_type, content_name = cli._content_type(f)
            rate = reader.line_rate_hz(f)
            n = int(f.num_lines)
            step = reader.position_step_m(f)
            has_seek = hasattr(f, "seek_lines")
            summary = {
                "name": f.path.name,
                "format": f.path.suffix.lower().lstrip("."),
                "mode": mode.value,
                "content_type": content_type,
                "content_type_name": content_name,
                "quantity": cli._format_quantity(f),
                "card": getattr(f, "card", None) or None,
                "format_version": getattr(f, "format_version", 0) or 0,
                "gauge_length_m": getattr(f, "gauge_length_m", None),
                "num_lines": n,
                "line_rate_hz": rate,
                "trig_frequency_hz": int(f.trig_frequency),
                "trig_frequency": cli._format_trig_frequency(f),
                "duration_s": n / rate if rate > 0 else 0.0,
                "start_time": cli._format_timestamp(f),
                "time_correction": cli._format_time_correction(f),
                "sample_rate_hz": int(f.sample_rate),
                "positions": int(f.positions_per_line),
                "step_m": step,
                "length_m": int(f.positions_per_line) * step,
                "compression": cli._format_compression(f),
            }
            channels = [name for name, (modes, *_rest) in CHANNELS.items() if mode in modes]
            default_channel = DEFAULT_CHANNEL[mode]
        job.check()

        views = []
        for view in VIEWS:
            if view == "scope" and not has_seek:
                views.append({"name": view, "available": False,
                              "reason": "the oscilloscope needs invisensing >= 1.1.0."})
            else:
                views.append({"name": view, "available": True, "reason": None})

        info = self._run_view(job, "info", path, None, "rad", {})
        units = [{"name": "rad", "available": True, "reason": None}]
        schema = view_schema()
        dests = {o["dest"] for o in schema.get("info", {}).get("options", [])}
        for dest, _quantity, _unit in UNITS:
            if dest not in dests:
                continue
            units.append({"name": dest, **self._unit_availability(path, dest)})

        self._send({
            "id": rid, "type": "file", "path": str(path), "summary": summary,
            "info_text": info["stdout"], "channels": channels,
            "default_channel": default_channel,
            "default_view": "scope" if mode is Mode.RAW else "heatmap",
            "views": views, "units": units, "warnings": info["warnings"],
        })

    @staticmethod
    def _unit_availability(path: str, unit: str) -> dict:
        """Whether the CLI opens ``path`` in ``unit``, in the CLI's words."""
        try:
            args = parse_request_args("info", path, unit=unit)
            with _captured(), cli._open(args):
                pass
        except AudaceDisplayError as e:
            return {"available": False, "reason": str(e)}
        except (ValueError, _OptionsError) as e:
            return {"available": False, "reason": str(e)}
        return {"available": True, "reason": None}

    # -- render ---------------------------------------------------------------

    def _run_view(self, job: _Job, view: str, path: str, channel, unit: str,
                  options: dict, *, keep_open: bool = False):
        """Runs the CLI's ``do_<view>``; returns what it recorded."""
        args = parse_request_args(view, path, channel=channel, unit=unit, options=options)
        do = getattr(cli, f"do_{view}")
        f = cli._open(args)
        try:
            total = None
            if view in _WINDOWED_VIEWS:
                with contextlib.suppress(AudaceDisplayError):
                    p0, p1 = reader.time_window_pulses(
                        f, args.start_time, args.duration, getattr(args, "max_pulses", None))
                    total = p1 - p0
            elif view == "info":
                total = min(1000, int(f.num_lines))
            with _instrumented(f, job, total), _recording() as rec, _captured() as out:
                do(f, args)
            job.check()
        except BaseException:
            f.close()
            raise
        if not keep_open:
            f.close()
        return {"recorder": rec, "stdout": out["stdout"], "warnings": out["warnings"],
                "file": f, "args": args}

    def _render(self, rid, req: dict, job: _Job) -> None:
        view = _field(req, "view", str)
        path = _field(req, "path", str)
        channel = req.get("channel")
        unit = req.get("unit") or "rad"
        options = req.get("options") or {}
        if not isinstance(options, dict):
            raise _OptionsError("'options' must be an object.")
        if view == "scope":
            self._render_scope(rid, job, path, channel, unit, options)
            return
        run = self._run_view(job, view, path, channel, unit, options)
        rec, warns = run["recorder"], run["warnings"]
        head = {"id": rid, "type": view, "view": view, "warnings": warns}
        arrays: list = []

        if view == "info":
            head["text"] = run["stdout"]
        elif view == "heatmap":
            a = rec.only("build_heatmap")
            lut, special = colormap_lut(a["cmap"])
            data = np.asarray(a["data"])
            head.update(title=a["title"], label=a["label"], t_extent=a["t_extent"],
                        d_extent=a["d_extent"], vmin=a["vmin"], vmax=a["vmax"],
                        cmap=a["cmap"], **special)
            arrays = [("data", data), ("lut", lut)]
        elif view == "bandheatmaps":
            a = rec.only("build_band_heatmaps")
            panels = []
            for i, panel in enumerate(a["panels"]):
                lut, special = colormap_lut(panel["cmap"])
                panels.append({"title": panel["title"], "label": panel["label"],
                               "vmin": panel["vmin"], "vmax": panel["vmax"],
                               "cmap": panel["cmap"], **special})
                arrays += [(f"panel{i}", np.asarray(panel["data"])), (f"lut{i}", lut)]
            head.update(title=a["suptitle"], t_extent=a["t_extent"], d_extent=a["d_extent"],
                        panels=panels)
        elif view == "fft":
            a = rec.only("build_fft")
            arrays = [("freqs", a["freqs"])]
            arrays += [(f"curve{i}", y) for i, (_label, y) in enumerate(a["curves"])]
            head.update(title=a["title"], y_label=a["y_label"], nyquist=a["nyquist"],
                        fmax=a["fmax"], curves=[label for label, _y in a["curves"]])
        elif view == "trace":
            a = rec.only("build_trace")
            arrays = [("times", a["centers_s"]), ("lo", a["lo"]), ("hi", a["hi"])]
            head.update(title=a["title"], label=a["label"])
        elif view == "inspect":
            a = rec.only("build_inspect")
            arrays = [("times", a["times"]), ("waveform", a["waveform"]),
                      ("freqs", a["freqs"]), ("magnitude", a["magnitude"])]
            head.update(title=a["title"], y_label=a["y_label"], ylim=a["ylim"],
                        stats_text=a["stats_text"], fft_y_label=a["fft_y_label"],
                        fft_log=a["fft_log"], fmax=a["fmax"])
        else:  # pragma: no cover -- build_argv refuses an unknown view first
            raise _OptionsError(f"unknown view '{view}'.")
        self._send(head, tuple(arrays))

    def _render_scope(self, rid, job: _Job, path, channel, unit, options) -> None:
        run = self._run_view(job, "scope", path, channel, unit, options, keep_open=True)
        try:
            a = run["recorder"].only("animate_scope")
        except BaseException:
            run["file"].close()
            raise
        self.close()  # one oscilloscope at a time
        params = {k: a[k] for k in ("get_y", "n_window", "trig_freq", "speed", "fps", "d_step",
                                    "length_m", "y_label", "title", "ylim", "max_points",
                                    "line_offset")}
        self._scope = _ScopeSession(rid, run["file"], params)
        first = max(0, min(int(run["args"].line), max(int(params["n_window"]) - 1, 0)))
        frame_head, frame_arrays = self._scope.frame(first)
        head = {"id": rid, "type": "scope", "view": "scope", "warnings": run["warnings"],
                "session": rid, "frame": frame_head}
        head.update({k: v for k, v in params.items() if k != "get_y"})
        self._send(head, frame_arrays)

    def _scope_frame(self, rid, req: dict) -> None:
        session = self._scope
        if session is None or session.id != req.get("session"):
            self._error(rid, "protocol", "no such oscilloscope session (render the scope again).")
            return
        head, arrays = session.frame(_field(req, "local", int))
        head.update(id=rid, type="scope_frame")
        self._send(head, arrays)


def serve(stdin_fd: int, stdout) -> int:
    """The worker loop: requests from the file descriptor ``stdin_fd``,
    messages to the binary stream ``stdout``, until ``shutdown`` or the end of
    the input."""
    channel = _Channel(stdout)
    inbox = _Inbox(stdin_fd)
    worker = Worker(channel.send, inbox.is_cancelled)
    try:
        while True:
            kind, item = inbox.get()
            if kind == "eof":
                return 0
            if kind == "bad":
                worker._error(None, "protocol", item)
                continue
            try:
                if not worker.handle(item):
                    return 0
            finally:
                inbox.forget(item.get("id"))
    except BrokenPipeError:  # the viewer went away
        return 0
    finally:
        worker.close()


def main() -> int:
    # The protocol owns the real stdout; anything else printed to it (a print
    # in a library, a C extension) lands in stderr instead.
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    out = os.fdopen(protocol_fd, "wb", buffering=0)
    return serve(sys.stdin.fileno(), out)


if __name__ == "__main__":
    sys.exit(main())
