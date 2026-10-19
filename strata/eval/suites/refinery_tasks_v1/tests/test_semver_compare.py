import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/semver_compare.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_semver_compare", path)
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


def test_spec_example_chain():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for lo, hi in zip(chain, chain[1:]):
        assert mod.compare(lo, hi) == -1, (lo, hi)
        assert mod.compare(hi, lo) == 1, (hi, lo)

def test_core_numeric_not_lexical():
    assert mod.compare("1.10.0", "1.9.0") == 1
    assert mod.compare("2.0.0", "10.0.0") == -1
    assert mod.compare("1.0.10", "1.0.9") == 1

def test_equal_and_build_metadata_ignored():
    assert mod.compare("1.2.3", "1.2.3") == 0
    assert mod.compare("1.2.3+build.5", "1.2.3+other") == 0
    assert mod.compare("1.0.0-alpha+001", "1.0.0-alpha") == 0

def test_numeric_identifier_below_alphanumeric():
    assert mod.compare("1.0.0-1", "1.0.0-a") == -1
    assert mod.compare("1.0.0-rc.9", "1.0.0-rc.10") == -1
    assert mod.compare("1.0.0-alpha.9", "1.0.0-alpha.x") == -1

def test_prerelease_ascii_order():
    assert mod.compare("1.0.0-Beta", "1.0.0-alpha") == -1
    assert mod.compare("1.0.0-alpha-1", "1.0.0-alpha") == 1

def test_invalid_versions():
    for bad in ["1.0", "1.0.0.0", "01.0.0", "1.02.0", "1.0.0-", "1.0.0-alpha..1", "1.0.0-01", "a.b.c", "", "1.0.-1"]:
        with pytest.raises(ValueError):
            mod.compare(bad, "1.0.0")
