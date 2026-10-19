import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/calc_expr.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_calc_expr", path)
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


def test_precedence_and_associativity():
    assert mod.evaluate("2+3*4") == 14
    assert mod.evaluate("(2+3)*4") == 20
    assert mod.evaluate("10-4-3") == 3
    assert mod.evaluate("100/10/5") == 2
    assert mod.evaluate("2*3+4*5-6/2") == 23

def test_truncating_division():
    assert mod.evaluate("7/2") == 3
    assert mod.evaluate("-7/2") == -3
    assert mod.evaluate("7/-2") == -3
    assert mod.evaluate("-7/-2") == 3
    assert mod.evaluate("6/4") == 1

def test_unary_minus():
    assert mod.evaluate("--3") == 3
    assert mod.evaluate("2*-3") == -6
    assert mod.evaluate("3+-2") == 1
    assert mod.evaluate("-(2+3)") == -5
    assert mod.evaluate("-2*-(1-4)") == -6

def test_whitespace_and_multidigit():
    assert mod.evaluate(" 12 *\t( 3 + 40 ) ") == 516

def test_division_by_zero():
    with pytest.raises(ZeroDivisionError):
        mod.evaluate("1/(2-2)")

def test_malformed():
    for bad in ["", "1+", "(1+2", "1+2)", "()", "2++3", "12 34", "1.5+2", "abc", "*3"]:
        with pytest.raises(ValueError):
            mod.evaluate(bad)

def test_no_eval():
    import re
    src = _source()
    assert not re.search(r"(?<![\w.])(eval|exec|compile)\s*\(", src)
    assert not _imports("ast")
