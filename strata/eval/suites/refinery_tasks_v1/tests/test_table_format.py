import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/table_format.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_table_format", path)
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


def test_basic_table():
    rows = [["name", "qty"], ["apple", "3"], ["kiwi", "12"]]
    assert mod.render(rows) == "name  | qty\n------+----\napple | 3\nkiwi  | 12"

def test_trailing_spaces_stripped():
    rows = [["a", "b"], ["long", ""]]
    assert mod.render(rows) == "a    | b\n-----+--\nlong |"

def test_short_rows_padded():
    rows = [["x", "y", "z"], ["1"]]
    assert mod.render(rows) == "x | y | z\n--+---+--\n1 |   |"

def test_header_only():
    assert mod.render([["h1", "h2"]]) == "h1 | h2\n---+---"

def test_empty_input():
    assert mod.render([]) == ""

def test_zero_width_column():
    rows = [["a", "", "c"], ["b", "", "d"]]
    assert mod.render(rows) == "a |  | c\n--+--+--\nb |  | d"
