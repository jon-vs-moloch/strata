import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/rle_codec.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_rle_codec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
mod = _load()

def _source():
    return pathlib.Path(TARGET).read_text()

def _imports(name):
    # True when the module source imports `name` (comments and docstrings do not count).
    import re
    return re.search(r"^\s*(import|from)\s+" + re.escape(name) + r"\b", _source(), re.M) is not None


def test_encode():
    assert mod.encode("aaab") == "3a1b"
    assert mod.encode("a" * 12) == "12a"
    assert mod.encode("") == ""
    assert mod.encode("abc") == "1a1b1c"
    assert mod.encode("  !!") == "2 2!"

def test_decode():
    assert mod.decode("3a1b") == "aaab"
    assert mod.decode("12a") == "a" * 12
    assert mod.decode("") == ""
    assert mod.decode("2 10x") == "  " + "x" * 10

def test_round_trip():
    for s in ["", "a", "aabbbcccc", "x" * 101 + "y", "hello  world!!", "ééé--"]:
        assert mod.decode(mod.encode(s)) == s

def test_encode_rejects_digits():
    with pytest.raises(ValueError):
        mod.encode("ab1")

def test_decode_malformed():
    for bad in ["3", "a", "3ab", "0a", "2a3", "aab", "00a"]:
        with pytest.raises(ValueError):
            mod.decode(bad)
