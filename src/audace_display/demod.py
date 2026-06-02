"""Demodulation via an **external plugin**.

audace-display contains *no* demodulation code. It only defines an **API
contract** and loads a user-provided Python script (outside the package, never
published to PyPI) that implements it. The result is displayed as a demodulated
file (heatmap).

Contract
========

The script must define **either** a ``Demodulator`` class (preferred, supports
stateful filters across chunks) **or** a ``demodulate`` function:

.. code-block:: python

    import numpy as np

    # Option A -- stateless function (applied chunk by chunk)
    def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
        '''chunk: (rows, line_size) raw  ->  (rows, positions) float32.'''
        ...

    # Option B -- stateful class (inter-chunk continuity guaranteed)
    class Demodulator:
        OUTPUT_LABEL = "phase (rad)"   # colorbar label (optional)
        IS_ANGULAR   = True            # diverging colormap centered on 0 (optional)

        def __init__(self, *, sample_rate, trig_frequency, line_size, meta):
            ...

        def process(self, chunk):
            '''Called sequentially on contiguous time chunks.'''
            ...

Guarantees provided to the plugin
---------------------------------
- ``process`` / ``demodulate`` receives pulse chunks that are **contiguous in
  time**, **in order**, with the **same number of positions** -> compatible with
  a stateful filter (IIR, moving average, ...).
- ``chunk`` is the raw buffer ``(rows, line_size)`` as returned by
  ``invisensing.File.read_lines`` (the file's dtype).
- ``meta`` is a dict of header metadata (see :func:`_build_meta`).

Output expectations
-------------------
- A **2-D** numpy array ``(rows, positions)``, ``rows`` identical to the input.
- Converted to ``float32`` for display.
- ``positions`` constant from one chunk to the next.

Optional attributes (class or function): ``OUTPUT_LABEL`` (str, colorbar label)
and ``IS_ANGULAR`` (bool, selects a diverging colormap).
"""
from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ._errors import AudaceDisplayError

Transform = Callable[[np.ndarray], np.ndarray]


@dataclass
class DemodPlugin:
    """Loaded plugin, ready to be plugged into the reader."""

    transform: Transform   # raw chunk -> (rows, positions) float32
    label: str             # colorbar label
    is_angular: bool       # diverging colormap?
    source: str            # script path (for messages)


def _build_meta(f) -> dict:
    """Metadata passed to the plugin."""
    return {
        "sample_rate": f.sample_rate,
        "trig_frequency": f.trig_frequency,
        "line_size": f.line_size,
        "positions_per_line": f.positions_per_line,
        "sample_size": f.sample_size,
        "range_v": f.range,
        "flags": f.flags,
        "mode": f.mode.value,
        "timestamp": f.timestamp,
        "num_lines": f.num_lines,
    }


def _filter_kwargs(callable_obj, kwargs: dict) -> dict:
    """Keep only the kwargs accepted by ``callable_obj`` (unless it has ``**kw``)."""
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs
    allowed = {
        name
        for name, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    }
    return {k: v for k, v in kwargs.items() if k in allowed}


def _validate_output(out, raw: np.ndarray, source: str) -> np.ndarray:
    arr = np.asarray(out)
    if arr.ndim != 2:
        raise AudaceDisplayError(
            f"demod plugin '{source}': expected 2-D output, got {arr.shape}."
        )
    if arr.shape[0] != raw.shape[0]:
        raise AudaceDisplayError(
            f"demod plugin '{source}': output has {arr.shape[0]} rows for "
            f"{raw.shape[0]} input rows (must preserve the time axis)."
        )
    return arr.astype(np.float32, copy=False)


def load_demodulator(script_path: str | Path, f) -> DemodPlugin:
    """Import ``script_path`` and build a :class:`DemodPlugin`.

    Detects ``Demodulator`` (preferred) or ``demodulate``. Validates the output
    on each call. Raises :class:`AudaceDisplayError` on any problem.
    """
    path = Path(script_path)
    if not path.is_file():
        raise AudaceDisplayError(f"demod script not found: {path}")

    spec = importlib.util.spec_from_file_location("audace_user_demod", str(path))
    if spec is None or spec.loader is None:
        raise AudaceDisplayError(f"could not load demod script: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # error inside the user script
        raise AudaceDisplayError(f"failed to import '{path}': {exc}") from exc

    meta = _build_meta(f)
    init_kwargs = {
        "sample_rate": f.sample_rate,
        "trig_frequency": f.trig_frequency,
        "line_size": f.line_size,
        "meta": meta,
    }
    src = str(path)

    demod_cls = getattr(module, "Demodulator", None)
    if demod_cls is not None:
        try:
            inst = demod_cls(**_filter_kwargs(demod_cls.__init__, init_kwargs))
        except Exception as exc:
            raise AudaceDisplayError(
                f"failed to instantiate Demodulator in '{path}': {exc}"
            ) from exc
        proc = getattr(inst, "process", None)
        if not callable(proc):
            raise AudaceDisplayError(
                f"'{path}': the Demodulator class must define a process(chunk) method."
            )
        label = str(getattr(inst, "OUTPUT_LABEL", getattr(demod_cls, "OUTPUT_LABEL", "demodulated")))
        is_ang = bool(getattr(inst, "IS_ANGULAR", getattr(demod_cls, "IS_ANGULAR", False)))

        def transform(raw: np.ndarray) -> np.ndarray:
            return _validate_output(proc(raw), raw, src)

        return DemodPlugin(transform, label, is_ang, src)

    fn = getattr(module, "demodulate", None)
    if fn is None:
        raise AudaceDisplayError(
            f"'{path}' must define a `Demodulator` class or a `demodulate` function."
        )
    if not callable(fn):
        raise AudaceDisplayError(f"'{path}': `demodulate` is not callable.")
    # Metadata: function attribute, else module global.
    label = str(
        getattr(fn, "OUTPUT_LABEL", None)
        or getattr(module, "OUTPUT_LABEL", None)
        or "demodulated"
    )
    is_ang = bool(
        getattr(fn, "IS_ANGULAR", None)
        if getattr(fn, "IS_ANGULAR", None) is not None
        else getattr(module, "IS_ANGULAR", False)
    )
    fn_kwargs = _filter_kwargs(fn, init_kwargs)

    def transform(raw: np.ndarray) -> np.ndarray:
        return _validate_output(fn(raw, **fn_kwargs), raw, src)

    return DemodPlugin(transform, label, is_ang, src)
