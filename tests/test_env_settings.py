import pytest

from strata.core.env_settings import env_int, env_seconds


def test_env_seconds_returns_default_when_unset_or_empty(monkeypatch):
    monkeypatch.delenv("STRATA_TEST_TIMEOUT_S", raising=False)
    assert env_seconds("STRATA_TEST_TIMEOUT_S", 90.0) == 90.0
    monkeypatch.setenv("STRATA_TEST_TIMEOUT_S", "")
    assert env_seconds("STRATA_TEST_TIMEOUT_S", 90.0) == 90.0


def test_env_seconds_parses_value(monkeypatch):
    monkeypatch.setenv("STRATA_TEST_TIMEOUT_S", "12.5")
    assert env_seconds("STRATA_TEST_TIMEOUT_S", 90.0) == 12.5


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "nan", "inf"])
def test_env_seconds_falls_back_to_default_on_invalid_value(monkeypatch, raw):
    monkeypatch.setenv("STRATA_TEST_TIMEOUT_S", raw)
    assert env_seconds("STRATA_TEST_TIMEOUT_S", 90.0) == 90.0


def test_env_int_allows_zero(monkeypatch):
    monkeypatch.setenv("STRATA_TEST_BUDGET", "0")
    assert env_int("STRATA_TEST_BUDGET", 7) == 0


def test_env_int_falls_back_to_default_on_invalid_value(monkeypatch):
    monkeypatch.setenv("STRATA_TEST_BUDGET", "-1")
    assert env_int("STRATA_TEST_BUDGET", 3) == 3
    monkeypatch.setenv("STRATA_TEST_BUDGET", "x")
    assert env_int("STRATA_TEST_BUDGET", 3) == 3
