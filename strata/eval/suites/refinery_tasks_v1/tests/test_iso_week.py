import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/iso_week.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_iso_week", path)
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


def test_spec_boundaries():
    assert mod.iso_week("2021-01-03") == (2020, 53)
    assert mod.iso_week("2024-12-30") == (2025, 1)
    assert mod.iso_week("2026-12-31") == (2026, 53)

def test_more_boundaries():
    assert mod.iso_week("2021-01-04") == (2021, 1)
    assert mod.iso_week("2027-01-01") == (2026, 53)
    assert mod.iso_week("2026-01-01") == (2026, 1)
    assert mod.iso_week("2025-12-29") == (2026, 1)
    assert mod.iso_week("2016-01-03") == (2015, 53)
    assert mod.iso_week("2019-12-30") == (2020, 1)
    assert mod.iso_week("2008-12-29") == (2009, 1)
    assert mod.iso_week("2010-01-03") == (2009, 53)

def test_mid_year():
    assert mod.iso_week("2026-06-15") == (2026, 25)

def test_bad_date():
    with pytest.raises(ValueError):
        mod.iso_week("2026-02-30")

def test_no_library_shortcut():
    import re
    src = _source()
    assert not re.search(r"\.isocalendar\s*\(", src)
    assert not re.search(r"\.strftime\s*\(", src)
