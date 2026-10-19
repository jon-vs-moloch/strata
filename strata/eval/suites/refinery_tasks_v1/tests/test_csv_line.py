import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/csv_line.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_csv_line", path)
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


def test_simple():
    assert mod.parse_line("a,b,c") == ["a", "b", "c"]

def test_empty_fields():
    assert mod.parse_line("") == [""]
    assert mod.parse_line("a,") == ["a", ""]
    assert mod.parse_line(",,") == ["", "", ""]
    assert mod.parse_line(" a , b ") == [" a ", " b "]

def test_quoted_fields():
    assert mod.parse_line('"a,b",c') == ["a,b", "c"]
    assert mod.parse_line('"say ""hi""",x') == ['say "hi"', "x"]
    assert mod.parse_line('""') == [""]
    assert mod.parse_line('x,""') == ["x", ""]

def test_quote_inside_unquoted_field_is_literal():
    assert mod.parse_line('ab"c,d') == ['ab"c', "d"]

def test_unterminated_quote():
    with pytest.raises(ValueError):
        mod.parse_line('"abc,d')

def test_text_after_closing_quote():
    with pytest.raises(ValueError):
        mod.parse_line('"ab"c,d')

def test_no_csv_module():
    assert not _imports("csv")
