import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/int_to_english.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_int_to_english", path)
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


def test_small():
    assert mod.to_words(0) == "zero"
    assert mod.to_words(7) == "seven"
    assert mod.to_words(13) == "thirteen"
    assert mod.to_words(19) == "nineteen"

def test_tens():
    assert mod.to_words(20) == "twenty"
    assert mod.to_words(21) == "twenty-one"
    assert mod.to_words(40) == "forty"
    assert mod.to_words(99) == "ninety-nine"

def test_hundreds():
    assert mod.to_words(100) == "one hundred"
    assert mod.to_words(101) == "one hundred one"
    assert mod.to_words(342) == "three hundred forty-two"
    assert mod.to_words(910) == "nine hundred ten"

def test_thousands_and_millions():
    assert mod.to_words(1000) == "one thousand"
    assert mod.to_words(1001) == "one thousand one"
    assert mod.to_words(12345) == "twelve thousand three hundred forty-five"
    assert mod.to_words(1000000) == "one million"
    assert mod.to_words(1000010) == "one million ten"
    assert mod.to_words(100000010) == "one hundred million ten"
    assert mod.to_words(2000300) == "two million three hundred"

def test_spec_example_and_max():
    assert mod.to_words(123456789) == "one hundred twenty-three million four hundred fifty-six thousand seven hundred eighty-nine"
    assert mod.to_words(999999999) == "nine hundred ninety-nine million nine hundred ninety-nine thousand nine hundred ninety-nine"

def test_out_of_range():
    for bad in (-1, 1000000000):
        with pytest.raises(ValueError):
            mod.to_words(bad)
