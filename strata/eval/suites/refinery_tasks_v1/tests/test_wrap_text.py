import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/wrap_text.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_wrap_text", path)
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


def test_basic_greedy():
    assert mod.wrap("the quick brown fox jumps", 10) == ["the quick", "brown fox", "jumps"]

def test_exact_width_fits():
    assert mod.wrap("abcde fghij", 5) == ["abcde", "fghij"]
    assert mod.wrap("ab cd", 5) == ["ab cd"]

def test_whitespace_runs_collapse():
    assert mod.wrap("  a\t\tb \n c  ", 3) == ["a b", "c"]

def test_empty():
    assert mod.wrap("", 4) == []
    assert mod.wrap(" \n\t ", 4) == []

def test_long_word_split():
    assert mod.wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]

def test_long_word_flushes_current_line_and_tail_stays_open():
    assert mod.wrap("hi abcdefghij k", 4) == ["hi", "abcd", "efgh", "ij k"]
    assert mod.wrap("abcdefgh xy", 4) == ["abcd", "efgh", "xy"]

def test_width_one():
    assert mod.wrap("ab c", 1) == ["a", "b", "c"]

def test_bad_width():
    for w in (0, -3):
        with pytest.raises(ValueError):
            mod.wrap("abc", w)
