import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/bowling.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_bowling", path)
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


def test_spec_examples():
    assert mod.score("X X X X X X X X X XXX") == 300
    assert mod.score("9- 9- 9- 9- 9- 9- 9- 9- 9- 9-") == 90
    assert mod.score("5/ 5/ 5/ 5/ 5/ 5/ 5/ 5/ 5/ 5/5") == 150

def test_gutter_and_open_frames():
    assert mod.score("-- -- -- -- -- -- -- -- -- --") == 0
    assert mod.score("12 34 52 7- -9 11 23 45 6- 8-") == 63

def test_mixed_game():
    rolls = "X 72 9/ X X 7/ 8- X 53 X81"
    # 1: X + 7 + 2 = 19          (19)
    # 2: 7 + 2 = 9               (28)
    # 3: 9/ + X = 20             (48)
    # 4: X + X + 7 = 27          (75)
    # 5: X + 7 + 3 = 20          (95)
    # 6: 7/ + 8 = 18             (113)
    # 7: 8 + 0 = 8               (121)
    # 8: X + 5 + 3 = 18          (139)
    # 9: 5 + 3 = 8               (147)
    # 10: X 8 1 = 19             (166)
    assert mod.score(rolls) == 166

def test_tenth_frame_spare_bonus():
    assert mod.score("-- -- -- -- -- -- -- -- -- 7/3") == 13
    assert mod.score("-- -- -- -- -- -- -- -- -- 7/X") == 20

def test_tenth_frame_strikes():
    assert mod.score("-- -- -- -- -- -- -- -- -- X5/") == 20
    assert mod.score("-- -- -- -- -- -- -- -- X X54") == 25 + 19

def test_malformed():
    for bad in ["", "X X X", "X X X X X X X X X X X X", "66 -- -- -- -- -- -- -- -- --", "/5 -- -- -- -- -- -- -- -- --",
                "A- -- -- -- -- -- -- -- -- --", "-- -- -- -- -- -- -- -- -- XX", "-- -- -- -- -- -- -- -- -- 7/",
                "-- -- -- -- -- -- -- -- -- 12X", "--  -- -- -- -- -- -- -- -- --"]:
        with pytest.raises(ValueError):
            mod.score(bad)
