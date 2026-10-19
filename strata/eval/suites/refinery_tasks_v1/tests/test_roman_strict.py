import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/roman_strict.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_roman_strict", path)
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


def test_valid_values():
    cases = {"I": 1, "IV": 4, "IX": 9, "XIV": 14, "XL": 40, "XC": 90, "CD": 400, "CM": 900,
             "MCMXCIV": 1994, "MMXXVI": 2026, "MMMCMXCIX": 3999, "LXXXVIII": 88, "CDXLIV": 444}
    for s, v in cases.items():
        assert mod.parse(s) == v, s

def test_non_canonical_rejected():
    for bad in ["IIII", "VV", "IC", "IL", "XM", "VX", "IIV", "XXL", "MMMM", "XXXX", "DD", "LC", "IXI", "CMC"]:
        with pytest.raises(ValueError):
            mod.parse(bad)

def test_bad_characters_rejected():
    for bad in ["", "iv", "XIIA", " X", "X "]:
        with pytest.raises(ValueError):
            mod.parse(bad)

def test_every_value_round_trips():
    # Every value 1..3999 has exactly one canonical form, which must parse back to itself.
    pairs = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    for n in range(1, 4000):
        x, s = n, ""
        for v, sym in pairs:
            while x >= v:
                s += sym; x -= v
        assert mod.parse(s) == n
