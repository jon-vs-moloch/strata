import importlib.util, os, pathlib, pytest
TARGET = os.environ.get("REFINERY_TARGET") or "workspace/refinery_tasks_v1/ledger.py"
def _load():
    path = pathlib.Path(TARGET).resolve()
    spec = importlib.util.spec_from_file_location("refinery_target_ledger", path)
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


def test_basic_flow():
    assert mod.apply(["open alice", "deposit alice 500", "withdraw alice 200"]) == {"alice": 300}

def test_overdraft_skipped():
    assert mod.apply(["open a", "deposit a 100", "withdraw a 101", "withdraw a 100"]) == {"a": 0}

def test_transfer_rules():
    cmds = ["open a", "open b", "deposit a 50", "transfer a b 30", "transfer a b 30", "transfer b a 10"]
    assert mod.apply(cmds) == {"a": 30, "b": 20}

def test_unopened_accounts_skipped():
    cmds = ["deposit ghost 10", "open a", "deposit a 5", "transfer a ghost 5", "transfer ghost a 1", "withdraw ghost 1"]
    assert mod.apply(cmds) == {"a": 5}

def test_reopen_ignored():
    assert mod.apply(["open a", "deposit a 7", "open a"]) == {"a": 7}

def test_opened_with_zero_and_order_free():
    assert mod.apply(["open z", "open y"]) == {"z": 0, "y": 0}

def test_large_amounts():
    big = 10 ** 30
    assert mod.apply(["open a", "deposit a %d" % big, "deposit a %d" % big, "withdraw a 1"]) == {"a": 2 * big - 1}

def test_empty():
    assert mod.apply([]) == {}
