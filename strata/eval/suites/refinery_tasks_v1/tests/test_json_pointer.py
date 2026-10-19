import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/json_pointer.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_json_pointer", path)
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


DOC = {"foo": ["bar", "baz"], "": 0, "a/b": 1, "c%d": 2, "e^f": 3, "g|h": 4, "i\\j": 5,
       'k"l': 6, " ": 7, "m~n": 8, "nested": {"list": [{"x": 10}, {"x": 20}]}, "~1": "tilde-one"}

def test_rfc6901_examples():
    assert mod.resolve(DOC, "") is DOC
    assert mod.resolve(DOC, "/foo") == ["bar", "baz"]
    assert mod.resolve(DOC, "/foo/0") == "bar"
    assert mod.resolve(DOC, "/") == 0
    assert mod.resolve(DOC, "/a~1b") == 1
    assert mod.resolve(DOC, "/c%d") == 2
    assert mod.resolve(DOC, "/ ") == 7
    assert mod.resolve(DOC, "/m~0n") == 8

def test_decode_order():
    # "~01" must decode to "~1" (replace ~1 first, then ~0), not to "/".
    assert mod.resolve(DOC, "/~01") == "tilde-one"

def test_nested():
    assert mod.resolve(DOC, "/nested/list/1/x") == 20

def test_list_index_rules():
    for bad in ("/foo/2", "/foo/01", "/foo/-", "/foo/x", "/foo/-1"):
        with pytest.raises(KeyError):
            mod.resolve(DOC, bad)

def test_missing_and_scalar():
    with pytest.raises(KeyError):
        mod.resolve(DOC, "/nope")
    with pytest.raises(KeyError):
        mod.resolve(DOC, "/foo/0/x")

def test_pointer_must_start_with_slash():
    with pytest.raises(ValueError):
        mod.resolve(DOC, "foo")
