from __future__ import annotations

from strata.eval import harness_eval


class DummyParameters:
    def __init__(self, value):
        self.value = value

    def peek_parameter(self, key, default_value=None):
        return self.value


class DummyStorage:
    def __init__(self, value):
        self.parameters = DummyParameters(value)

    def close(self):
        return None


def test_get_active_eval_harness_config_migrates_legacy_default(monkeypatch):
    monkeypatch.setattr(
        harness_eval,
        "StorageManager",
        lambda: DummyStorage(
            {
                "system_prompt": "base prompt",
                "context_files": ["README.md", "docs/spec/project-philosophy.md"],
            }
        ),
    )
    config = harness_eval.get_active_eval_harness_config()
    assert config["system_prompt"] == "base prompt"
    assert config["context_files"] == harness_eval.DEFAULT_CONTEXT_FILES
