"""``audace-display gui``: finds the desktop viewer (the audace-display-gui
package) and starts it with this interpreter, or says how to install it."""
import os
import stat
import sys

import pytest

from audace_display import cli, gui_launcher

posix_only = pytest.mark.skipif(os.name == "nt", reason="a shell script stands in for the viewer")


@pytest.fixture
def no_viewer(monkeypatch, tmp_path):
    """No viewer anywhere: no override, empty script dirs, nothing on PATH."""
    monkeypatch.delenv(gui_launcher.BINARY_ENV, raising=False)
    monkeypatch.setattr(gui_launcher, "_script_dirs", lambda: [str(tmp_path / "empty")])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))


@pytest.fixture
def fake_viewer(tmp_path, monkeypatch):
    """A stand-in viewer that writes its arguments, one per line, and exits 3."""
    record = tmp_path / "args.txt"
    exe = tmp_path / "bin" / "audace-display-gui"
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\nexit 3\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.delenv(gui_launcher.BINARY_ENV, raising=False)
    monkeypatch.setattr(gui_launcher, "_script_dirs", lambda: [str(exe.parent)])
    return exe, record


def test_a_missing_viewer_says_how_to_install_it(no_viewer, capsys):
    assert cli.main(["gui", "acq.h5"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: the desktop viewer is not installed")
    assert 'pip install "audace-display[gui]"' in err
    assert err.count("\n") == 1, "one clear line"


def test_a_wrong_override_is_named(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(gui_launcher.BINARY_ENV, str(tmp_path / "nowhere"))
    assert cli.main(["gui"]) == 1
    assert "AUDACE_DISPLAY_GUI=" in capsys.readouterr().err


@posix_only
def test_the_viewer_starts_with_this_interpreter(fake_viewer):
    exe, record = fake_viewer
    code = cli.main(["gui", "acq.h5", "--lang", "fr", "--set", "max_time_bins=500"])
    assert code == 3, "the viewer's exit code"
    assert record.read_text().splitlines() == [
        "acq.h5", "--lang", "fr", "--set", "max_time_bins=500",
        "--python", f'"{sys.executable}"',
    ]


@posix_only
def test_an_interpreter_given_is_kept(fake_viewer):
    _, record = fake_viewer
    cli.main(["gui", "--python", "py -3", "acq.h5"])
    assert record.read_text().splitlines() == ["--python", "py -3", "acq.h5"]


@posix_only
def test_the_override_and_path_are_tried(fake_viewer, monkeypatch, tmp_path):
    exe, _ = fake_viewer
    monkeypatch.setattr(gui_launcher, "_script_dirs", lambda: [str(tmp_path / "empty")])
    monkeypatch.setenv("PATH", str(exe.parent))
    assert gui_launcher.find_binary() == str(exe)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert gui_launcher.find_binary() is None
    monkeypatch.setenv(gui_launcher.BINARY_ENV, str(exe))
    assert gui_launcher.find_binary() == str(exe)


def test_help_is_printed_here_without_the_viewer(no_viewer, capsys):
    # The viewer has no console on Windows: its help would be lost.
    assert cli.main(["gui", "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: audace-display gui [FILE] [options]")
    assert 'pip install "audace-display[gui]"' in out and "--screenshot" in out


def test_option_values_are_not_taken_for_flags():
    assert gui_launcher._scan(["--set", "x=-h", "f.h5"]) == (False, False)
    assert gui_launcher._scan(["--view", "--python"]) == (False, False)
    assert gui_launcher._scan(["f.h5", "-h"]) == (True, False)
    assert gui_launcher._scan(["--python", "python3"]) == (False, True)


def test_the_interpreter_is_one_word_of_the_viewers_command():
    assert gui_launcher.quote_command(r"C:\Program Files\Python312\python.exe") == (
        r'"C:\Program Files\Python312\python.exe"'
    )
    assert gui_launcher.quote_command('/opt/a"b/python') == "'/opt/a\"b/python'"
    assert gui_launcher.quote_command("/x/'\"/python") is None


def test_the_cli_lists_the_viewer(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "gui" in out and "Desktop viewer" in out
