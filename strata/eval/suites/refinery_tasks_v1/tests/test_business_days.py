import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/business_days.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_business_days", path)
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

# Calendar facts used: 2026-10-16 is a Friday, 2026-10-17 Saturday, 2026-10-19 Monday,
# 2026-12-24 Thursday, 2026-12-25 Friday, 2026-12-28 Monday, 2027-01-01 Friday.

def test_forward_over_weekend():
    assert mod.add_business_days("2026-10-16", 1, []) == "2026-10-19"
    assert mod.add_business_days("2026-10-16", 5, []) == "2026-10-23"

def test_backward_over_weekend():
    assert mod.add_business_days("2026-10-19", -1, []) == "2026-10-16"
    assert mod.add_business_days("2026-10-17", -1, []) == "2026-10-16"

def test_zero():
    assert mod.add_business_days("2026-10-16", 0, []) == "2026-10-16"
    assert mod.add_business_days("2026-10-17", 0, []) == "2026-10-19"
    assert mod.add_business_days("2026-12-25", 0, ["2026-12-25"]) == "2026-12-28"

def test_holidays_skipped():
    assert mod.add_business_days("2026-12-24", 1, ["2026-12-25"]) == "2026-12-28"
    assert mod.add_business_days("2026-12-31", 1, ["2027-01-01"]) == "2027-01-04"
    assert mod.add_business_days("2026-12-28", -1, ["2026-12-25", "2026-12-25", "2026-12-26"]) == "2026-12-24"

def test_start_on_weekend_forward():
    assert mod.add_business_days("2026-10-17", 1, []) == "2026-10-19"

def test_month_and_leap_boundaries():
    assert mod.add_business_days("2028-02-28", 1, []) == "2028-02-29"
    assert mod.add_business_days("2026-01-30", 1, []) == "2026-02-02"

def test_malformed():
    for start, hol in (("2026-13-01", []), ("2026/10/16", []), ("2026-10-16", ["nope"])):
        with pytest.raises(ValueError):
            mod.add_business_days(start, 1, hol)
