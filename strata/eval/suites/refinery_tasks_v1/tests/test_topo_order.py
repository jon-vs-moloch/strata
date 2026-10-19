import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/topo_order.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_topo_order", path)
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


def test_empty():
    assert mod.order({}) == []

def test_simple_chain():
    assert mod.order({"c": ["b"], "b": ["a"]}) == ["a", "b", "c"]

def test_lexicographic_tie_break():
    assert mod.order({"b": [], "a": [], "c": []}) == ["a", "b", "c"]
    # After "a" is emitted, "d" (needs a) competes with "b" and "c": b < c < d.
    assert mod.order({"d": ["a"], "b": [], "c": [], "a": []}) == ["a", "b", "c", "d"]

def test_smallest_available_not_smallest_overall():
    # "a" depends on "z", so "z" must come first even though "a" < "z".
    assert mod.order({"a": ["z"], "m": []}) == ["m", "z", "a"]

def test_dependency_only_items_included_and_duplicates():
    assert mod.order({"app": ["lib", "lib", "core"], "lib": ["core"]}) == ["core", "lib", "app"]

def test_diamond():
    assert mod.order({"d": ["b", "c"], "b": ["a"], "c": ["a"]}) == ["a", "b", "c", "d"]

def test_cycles():
    for deps in ({"a": ["b"], "b": ["a"]}, {"a": ["a"]}, {"a": ["b"], "b": ["c"], "c": ["a"], "d": []}):
        with pytest.raises(ValueError):
            mod.order(deps)
