import numpy as np
import pytest
from invisensing import File

from audace_display.demod import load_demodulator
from audace_display import reader
from audace_display._errors import AudaceDisplayError

# --- Plugin scripts written to disk ------------------------------------------

FUNC_PLUGIN = '''
import numpy as np
OUTPUT_LABEL = "test (u)"
IS_ANGULAR = False
def demodulate(chunk, *, sample_rate, trig_frequency, line_size, meta):
    # interleaved magnitude: sqrt(I^2 + Q^2)
    i = chunk[:, 0::2].astype(np.float32)
    q = chunk[:, 1::2].astype(np.float32)
    return np.sqrt(i * i + q * q)
'''

CLASS_PLUGIN = '''
import numpy as np
class Demodulator:
    OUTPUT_LABEL = "phase-ish (rad)"
    IS_ANGULAR = True
    def __init__(self, *, sample_rate, trig_frequency, line_size, meta):
        self.calls = 0
        self.line_size = line_size
    def process(self, chunk):
        self.calls += 1
        return chunk[:, 0::2].astype(np.float32)
'''

BAD_SHAPE_PLUGIN = '''
import numpy as np
def demodulate(chunk, **kw):
    return chunk[0]  # 1-D: invalid
'''

NO_ENTRY_PLUGIN = '''
x = 1
'''


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_function_plugin(tmp_path, iq_dat):
    script = _write(tmp_path, "func_demod.py", FUNC_PLUGIN)
    with File(str(iq_dat)) as f:
        plugin = load_demodulator(script, f)
        assert plugin.label == "test (u)"
        assert plugin.is_angular is False
        raw = f.read_lines(10)
        out = plugin.transform(raw)
        assert out.shape == (10, raw.shape[1] // 2)
        assert out.dtype == np.float32
        # sqrt(I^2+Q^2) >= 0
        assert np.all(out >= 0)


def test_class_plugin_streams_in_order(tmp_path, iq_dat):
    script = _write(tmp_path, "class_demod.py", CLASS_PLUGIN)
    with File(str(iq_dat)) as f:
        plugin = load_demodulator(script, f)
        assert plugin.is_angular is True
        res = reader.load_decimated(f, plugin.transform, max_time_bins=20, max_space_bins=16)
        assert res.data.shape[0] <= 20


def test_bad_output_shape(tmp_path, iq_dat):
    script = _write(tmp_path, "bad.py", BAD_SHAPE_PLUGIN)
    with File(str(iq_dat)) as f:
        plugin = load_demodulator(script, f)
        with pytest.raises(AudaceDisplayError, match="2-D"):
            plugin.transform(f.read_lines(5))


def test_no_entry_point(tmp_path, iq_dat):
    script = _write(tmp_path, "noentry.py", NO_ENTRY_PLUGIN)
    with File(str(iq_dat)) as f:
        with pytest.raises(AudaceDisplayError, match="Demodulator|demodulate"):
            load_demodulator(script, f)


def test_missing_file(iq_dat):
    with File(str(iq_dat)) as f:
        with pytest.raises(AudaceDisplayError, match="not found"):
            load_demodulator("/does/not/exist.py", f)
