"""What ``info`` says a file IS, and the reading fixes it relies on.

The content type is the reference for every file that carries one: ``info``
prints it and no longer names a pipeline from the flags (the SCHUBERT flag is
deprecated). A legacy capture (content type 0) prints exactly what it always
did. Also: the card tag, the HDF5 compression, the UTC offset of absolute
times, the uint16 magnitude column, and the ``inspect --script`` probe.
"""
from __future__ import annotations

import time

import numpy as np
import pytest
from invisensing import (
    File,
    Header,
    FLAG_DEMODULATED,
    FLAG_FLOAT,
    FLAG_INTERLEAVED,
    FLAG_MOZART,
    FLAG_PHASE,
    FLAG_UNSIGNED,
    export_dat,
)
from invisensing._core import build_header_bytes

from audace_display import cli, reader
from audace_display.channels import resolve_channel

#: invisensing >= 1.4.0 as fixed on 2026-11-03: File.content_type_name,
#: File.card, File.compression, uint16 single-column UNSIGNED files.
needs_content_type_sdk = pytest.mark.skipif(
    not hasattr(File, "content_type_name"),
    reason="needs an invisensing that reads content types, cards and unsigned columns",
)


def _dat(path, data, *, flags, content_type, card=None, range_mv=1_000):
    kw = {} if card is None else {"card": card}
    h = Header(
        line_size=int(data.shape[1]), trig_frequency=1_000,
        sample_size=int(data.dtype.itemsize), sample_rate=125_000_000,
        flags=int(flags), range=range_mv, content_type=content_type,
        acquisition_ns=1_790_000_000_000_000_000, **kw,
    )
    path.write_bytes(build_header_bytes(h) + np.ascontiguousarray(data).tobytes())
    return path


def _info(path, capsys) -> str:
    assert cli.main(["info", str(path)]) == 0
    return capsys.readouterr().out


# --- info: legacy files unchanged ---------------------------------------------


def test_a_legacy_demodulated_file_prints_what_it_always_did(iq_dat, capsys):
    out = _info(iq_dat, capsys)
    assert "  Flags          : 0x101 (DEMODULATED | INTERLEAVED | SCHUBERT)\n" in out
    assert "Content type" not in out and "Card" not in out and "Compression" not in out


def test_a_legacy_mozart_file_still_says_mozart(tmp_path, capsys):
    path = tmp_path / "mozart_legacy.dat"
    export_dat(path, np.zeros((4, 8), np.float32), sample_rate=125_000_000,
               trig_frequency=1_000, flags=FLAG_DEMODULATED | FLAG_PHASE | FLAG_MOZART)
    out = _info(path, capsys)
    assert "  Flags          : 0x0C3 (DEMODULATED | FLOAT | PHASE | MOZART)\n" in out


# --- info: the content type replaces the pipeline -----------------------------


def test_a_content_type_is_printed_and_no_pipeline_is_named(tmp_path, capsys):
    path = _dat(tmp_path / "i.dat", np.zeros((4, 8), np.int16),
                flags=FLAG_DEMODULATED, content_type=13)
    out = _info(path, capsys)
    assert "  Content type   : hw_i\n" in out
    assert "  Flags          : 0x001 (DEMODULATED)\n" in out
    assert "SCHUBERT" not in out and "MOZART" not in out


def test_mozart_comes_from_the_content_type_only(tmp_path, capsys):
    flags = FLAG_DEMODULATED | FLAG_FLOAT | FLAG_PHASE | FLAG_MOZART
    path = _dat(tmp_path / "m.dat", np.zeros((4, 8), np.float32), flags=flags, content_type=9)
    out = _info(path, capsys)
    assert "  Content type   : sw_mozart (Mozart)\n" in out
    assert "  Flags          : 0x0C3 (DEMODULATED | FLOAT | PHASE)\n" in out
    # The MOZART bit on another content type names nothing.
    path = _dat(tmp_path / "dui.dat", np.zeros((4, 8), np.float32), flags=flags, content_type=4)
    out = _info(path, capsys)
    assert "  Content type   : hw_arctan_dui\n" in out
    assert "Mozart" not in out and "MOZART" not in out


@needs_content_type_sdk
def test_the_card_of_a_dual_card_run_is_printed(tmp_path, capsys):
    path = _dat(tmp_path / "c.dat", np.zeros((4, 8), np.int16),
                flags=0, content_type=1, card="pcie7818")
    out = _info(path, capsys)
    assert "  Card           : pcie7818\n" in out


@needs_content_type_sdk
def test_the_lossy_compression_and_its_bound_are_printed(tmp_path, capsys):
    h5py = pytest.importorskip("h5py")
    hdf5plugin = pytest.importorskip("hdf5plugin")
    path = tmp_path / "lossy.hdf5"
    with h5py.File(str(path), "w") as fp:
        fp.create_dataset("acoustic_data", data=np.zeros((8, 16), np.float32),
                          chunks=(4, 16), shuffle=True, **hdf5plugin.Zstd(clevel=1))
        for k, v in {"line_size": 16, "trig_frequency": 1_000, "sample_size": 4,
                     "sample_rate": 125_000_000, "content_type": 4,
                     "flags": FLAG_DEMODULATED | FLAG_FLOAT | FLAG_PHASE}.items():
            fp.attrs[k] = np.uint32(v)
        fp.attrs["quantization_step_rad"] = np.float64(2.0**-13)
        fp.attrs["quantization_max_error_rad"] = np.float64(2.0**-14)
    out = _info(path, capsys)
    assert ("  Compression    : ZSTD_LOSSY_1E4 "
            "(phase on a 1.221e-04 rad grid, error <= 6.104e-05 rad)\n") in out


# --- Absolute times carry their UTC offset ------------------------------------


@pytest.fixture
def tz(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is POSIX only")

    def set_tz(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_tz
    monkeypatch.undo()
    time.tzset()


def test_absolute_times_carry_the_utc_offset(tz):
    ns = 1_781_535_974_110_003_203  # 2026-06-15 15:06:14 UTC
    tz("UTC")
    assert cli._format_ns(ns) == "2026-06-15 15:06:14.110003203+00:00 (1781535974110003203 ns)"
    tz("Europe/Paris")
    assert cli._format_ns(ns) == "2026-06-15 17:06:14.110003203+02:00 (1781535974110003203 ns)"
    winter = 1_767_225_600_000_000_007  # 2026-01-01 00:00:00 UTC
    assert cli._format_ns(winter) == "2026-01-01 01:00:00.000000007+01:00 (1767225600000000007 ns)"
    tz("America/St_Johns")
    assert cli._format_ns(winter) == "2025-12-31 20:30:00.000000007-03:30 (1767225600000000007 ns)"


# --- The magnitude/ child of a split arctan/magnitude stream ------------------

MAGNITUDES = np.array([[40_000, 65_535, 32_768, 0, 32_767, 1, 50_000, 2]] * 6, np.uint16)


@needs_content_type_sdk
def test_a_split_magnitude_child_shows_its_real_values(tmp_path, capsys):
    path = _dat(tmp_path / "magnitude.dat", MAGNITUDES.view(np.int16),
                flags=FLAG_DEMODULATED | FLAG_UNSIGNED, content_type=5)
    volts = MAGNITUDES.astype(np.float64) * (1.0 / 32768.0)  # 1 V range
    with File(str(path)) as f:
        canonical, resolver, label, _ = resolve_channel(None, f.mode)
        assert canonical == "raw"
        got = resolver(f, f.read_lines(6))
        np.testing.assert_allclose(got, volts, rtol=1e-6)
        assert got[0, 0] == pytest.approx(40_000 / 32_768)  # 1.2207 V, not -0.78 V
        f.rewind()
        res = reader.load_decimated(f, lambda raw: resolver(f, raw), max_time_bins=6,
                                    max_space_bins=8)
        assert float(np.min(res.data)) >= 0.0
        assert float(np.max(res.data)) == pytest.approx(65_535 / 32_768)
    out = _info(path, capsys)
    assert "  Content type   : hw_amplitude\n" in out
    assert "min=+0  " in out and "max=+2\n" in out, out


# --- inspect --script: the streamed plugin sees one contiguous run ------------

CHUNK_LOG_PLUGIN = '''
import numpy as np

class Demodulator:
    OUTPUT_LABEL = "rows"

    def __init__(self, *, line_size, plugin_args=None, **kw):
        args = list(plugin_args or [])
        self.log = open(args[args.index("--log") + 1], "a")
        self.log.write(f"instance {id(self)}\\n")
        self.log.flush()

    def process(self, chunk):
        # Column 0 of row i holds i: log the rows this instance received.
        rows = chunk[:, 0].astype(np.int64)
        self.log.write(" ".join(str(int(r)) for r in rows) + "\\n")
        self.log.flush()
        return chunk.astype(np.float32)
'''


def test_inspect_script_streams_contiguous_in_order_chunks(tmp_path):
    rows = np.repeat(np.arange(5_000, dtype=np.int16)[:, None], 8, axis=1)
    data = tmp_path / "rows.dat"
    export_dat(data, rows, sample_rate=125_000_000, trig_frequency=1_000)
    plugin = tmp_path / "chunk_log.py"
    plugin.write_text(CHUNK_LOG_PLUGIN)
    log = tmp_path / "chunks.log"
    rc = cli.main([
        "inspect", str(data), "--script", str(plugin), "--index", "3",
        "--start-time", "2", "--duration", "1", "--save", str(tmp_path / "i.png"),
        "--log", str(log),
    ])
    assert rc == 0
    instances: list[list[int]] = []
    for line in log.read_text().splitlines():
        if line.startswith("instance "):
            instances.append([])
        elif line:
            instances[-1].extend(int(v) for v in line.split())
    # A throwaway instance probed the width; the one that streamed saw the
    # window [2000, 3000) and nothing else, in order.
    assert len(instances) == 2, instances
    assert instances[-1] == list(range(2_000, 3_000))
