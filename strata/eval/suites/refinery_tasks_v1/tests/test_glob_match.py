import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/glob_match.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_glob_match", path)
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


def test_literal_and_whole_string():
    assert mod.match("abc", "abc")
    assert not mod.match("abc", "abcd")
    assert not mod.match("abc", "ABC")

def test_star():
    assert mod.match("*", "")
    assert mod.match("a*", "a")
    assert mod.match("a*c", "abbbc")
    assert mod.match("*.py", "x.py")
    assert not mod.match("*.py", "x.pyc")
    assert mod.match("a*b*c", "aXbYbZc")
    assert not mod.match("a*b*c", "aXbYbZ")

def test_question_mark():
    assert mod.match("?", "x")
    assert not mod.match("?", "")
    assert not mod.match("a?c", "ac")
    assert mod.match("a??", "abc")

def test_sets_and_ranges():
    assert mod.match("[abc]", "b")
    assert not mod.match("[abc]", "d")
    assert mod.match("file[0-9].txt", "file7.txt")
    assert not mod.match("file[0-9].txt", "filex.txt")
    assert mod.match("[a-cx-z]", "y")

def test_negated_sets():
    assert mod.match("[!abc]", "d")
    assert not mod.match("[!abc]", "a")
    assert mod.match("[!0-9]x", "ax")
    assert not mod.match("[!0-9]x", "5x")

def test_specials_literal_inside_class():
    assert mod.match("[*?]", "*")
    assert mod.match("[*?]", "?")
    assert not mod.match("[*?]", "a")

def test_hyphen_at_class_edge_is_literal():
    assert mod.match("[-a]", "-")
    assert mod.match("[a-]", "-")
    assert mod.match("[a-]", "a")

def test_unclosed_bracket_is_literal():
    assert mod.match("a[b", "a[b")
    assert not mod.match("a[b", "ab")

def test_no_banned_modules():
    for name in ("fnmatch", "glob", "re"):
        assert not _imports(name), name
