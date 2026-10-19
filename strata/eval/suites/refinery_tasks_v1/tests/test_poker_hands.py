import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/poker_hands.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_poker_hands", path)
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


def test_category_order():
    ladder = ["2H 4D 7S 9C KD",   # high card
              "2H 2D 7S 9C KD",   # pair
              "2H 2D 7S 7C KD",   # two pair
              "2H 2D 2S 9C KD",   # three of a kind
              "3H 4D 5S 6C 7D",   # straight
              "2H 4H 7H 9H KH",   # flush
              "2H 2D 2S 9C 9D",   # full house
              "2H 2D 2S 2C KD",   # four of a kind
              "3S 4S 5S 6S 7S"]   # straight flush
    for lo, hi in zip(ladder, ladder[1:]):
        assert mod.compare(lo, hi) == -1, (lo, hi)
        assert mod.compare(hi, lo) == 1, (hi, lo)

def test_ace_low_straight_is_lowest_straight():
    assert mod.compare("AH 2D 3S 4C 5D", "2H 3D 4S 5C 6D") == -1
    assert mod.compare("AH 2D 3S 4C 5D", "KH QD JS 9C 8D") == 1
    assert mod.compare("TH JD QS KC AD", "9H TD JS QC KD") == 1

def test_high_card_kickers():
    assert mod.compare("2H 3D 5S 9C KD", "2C 3H 4S 9D KH") == 1
    assert mod.compare("2H 3D 5S 9C KD", "2D 3H 5C 9S KC") == 0

def test_pair_then_kickers():
    assert mod.compare("5H 5D 2S 3C 4D", "4H 4D AS KC QD") == 1
    assert mod.compare("5H 5D AS 3C 2D", "5C 5S KS QC JD") == 1
    assert mod.compare("5H 5D AS KC 2D", "5C 5S AH KD 3C") == -1

def test_two_pair_ordering():
    assert mod.compare("KH KD 2S 2C AD", "QH QD JS JC AH") == 1
    assert mod.compare("KH KD 3S 3C 2D", "KC KS 2H 2D AH") == 1
    assert mod.compare("KH KD 3S 3C 2D", "KC KS 3H 3D 4H") == -1

def test_full_house_by_trips():
    assert mod.compare("3H 3D 3S 2C 2D", "2H 2S 2C AH AD") == 1

def test_flush_card_by_card_and_suits_never_break_ties():
    assert mod.compare("2H 4H 7H 9H KH", "3S 4S 7S 9S KS") == -1
    assert mod.compare("2H 4H 7H 9H KH", "2S 4S 7S 9S KS") == 0

def test_invalid_hands():
    for bad in ["2H 3D 5S 9C", "2H 3D 5S 9C KD QH", "1H 3D 5S 9C KD", "2X 3D 5S 9C KD", "2H 2H 3D 5S 9C", "2H  3D 5S 9C KD"]:
        with pytest.raises(ValueError):
            mod.compare(bad, "2C 3H 4S 9D KH")
