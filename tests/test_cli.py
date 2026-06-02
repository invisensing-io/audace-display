"""End-to-end CLI tests (headless, PNG output)."""
import numpy as np
import pytest

from audace_display.cli import main


def _png_nonempty(path):
    return path.is_file() and path.stat().st_size > 0


def test_auto_raw_makes_line_graph(raw_dat, tmp_path):
    out = tmp_path / "raw.png"
    rc = main([str(raw_dat), "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_auto_demod_makes_heatmap(iq_dat, tmp_path):
    out = tmp_path / "iq.png"
    rc = main([str(iq_dat), "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_explicit_heatmap_db(arctan_dat, tmp_path):
    out = tmp_path / "h.png"
    rc = main(["heatmap", str(arctan_dat), "--db", "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_trace_subcommand(raw_dat, tmp_path):
    out = tmp_path / "t.png"
    rc = main(["trace", str(raw_dat), "--position", "5", "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_fft_subcommand(iq_dat, tmp_path):
    out = tmp_path / "f.png"
    rc = main(["fft", str(iq_dat), "--position", "10", "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_fft_all_fibre_average(phase_dat, tmp_path):
    out = tmp_path / "fa.png"
    rc = main(["fft", str(phase_dat), "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_info_runs(raw_dat, capsys):
    rc = main(["info", str(raw_dat)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Mode" in captured.out and "raw" in captured.out


def test_demod_subcommand(iq_dat, tmp_path):
    script = tmp_path / "d.py"
    script.write_text(
        "import numpy as np\n"
        "def demodulate(chunk, **kw):\n"
        "    return np.abs(chunk[:, 0::2].astype(np.float32))\n"
    )
    out = tmp_path / "d.png"
    rc = main(["demod", str(iq_dat), "--script", str(script), "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_unknown_channel_returns_error(raw_dat, capsys):
    rc = main(["heatmap", str(raw_dat), "--channel", "bogus"])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err


def test_missing_file_returns_error(tmp_path, capsys):
    rc = main([str(tmp_path / "nope.dat")])
    assert rc == 1


# --- Scope (animated oscilloscope) -------------------------------------------


def test_scope_save_static_frame(raw_dat, tmp_path):
    """--save freezes a line to PNG (no animation)."""
    out = tmp_path / "frame.png"
    rc = main(["scope", str(raw_dat), "--line", "10", "--save", str(out)])
    assert rc == 0
    assert _png_nonempty(out)


def test_scope_routes_to_mpl_animation(raw_dat, monkeypatch):
    """Without --save, routes to the matplotlib animation; get_y reads a line."""
    from audace_display import plotting
    captured = {}

    def fake_animate(get_y, n_window, **kw):
        captured["n"] = n_window
        captured["y"] = np.asarray(get_y(0))

    monkeypatch.setattr(plotting, "animate_scope", fake_animate)
    rc = main(["scope", str(raw_dat), "--backend", "matplotlib"])
    assert rc == 0
    assert captured["n"] > 0
    assert captured["y"].ndim == 1 and captured["y"].size > 0


def test_auto_raw_routes_to_scope(raw_dat, monkeypatch):
    """The default for a raw file is now the animated oscilloscope."""
    from audace_display import plotting
    hit = {}
    monkeypatch.setattr(plotting, "animate_scope", lambda *a, **k: hit.setdefault("ok", True))
    rc = main([str(raw_dat), "--backend", "matplotlib"])
    assert rc == 0
    assert hit.get("ok") is True


# --- Help / version ----------------------------------------------------------


def test_top_level_help_has_examples(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Examples" in out
    assert "audace-display heatmap acq.dat" in out
    assert "auto" in out and "demod" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "audace-display" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["auto", "info", "heatmap", "fft", "trace", "scope", "demod"])
def test_subcommand_help_has_example(cmd, capsys):
    with pytest.raises(SystemExit) as exc:
        main([cmd, "--help"])
    assert exc.value.code == 0
    assert "Example" in capsys.readouterr().out
