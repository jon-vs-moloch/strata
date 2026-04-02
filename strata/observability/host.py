"""
@module observability.host
@purpose Best-effort host telemetry for operator-comfort and runtime health decisions.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import time
from typing import Any, Dict


_HOST_CACHE: Dict[str, Any] = {"captured_at": 0.0, "snapshot": {}}


def _run_shell(command: str, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            ["sh", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return str(result.stdout or "").strip()
    except Exception:
        return ""


def _parse_memory_free_pct() -> float | None:
    raw = _run_shell("memory_pressure -Q 2>/dev/null | head -n 5", timeout=2.0)
    match = re.search(r"System-wide memory free percentage:\s*(\d+)%", raw)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _parse_thermal_status() -> Dict[str, Any]:
    raw = _run_shell("pmset -g therm 2>/dev/null", timeout=2.0)
    lowered = raw.lower()
    snapshot = {
        "raw": raw,
        "warning_level": "nominal",
        "performance_limited": False,
        "cpu_power_limited": False,
    }
    if not raw:
        snapshot["warning_level"] = "unknown"
        return snapshot
    if "no thermal warning level has been recorded" not in lowered and "warning" in lowered:
        snapshot["warning_level"] = "warning"
    if "no performance warning level has been recorded" not in lowered and "performance" in lowered:
        snapshot["performance_limited"] = True
        snapshot["warning_level"] = "warning"
    if "no cpu power status has been recorded" not in lowered and "cpu power" in lowered:
        snapshot["cpu_power_limited"] = True
        snapshot["warning_level"] = "warning"
    return snapshot


def _parse_cpu_usage() -> Dict[str, Any]:
    raw = _run_shell("ps -A -o %cpu | awk 'NR>1 {s+=$1} END {print s}'", timeout=2.0)
    cpu_count = max(1, int(os.cpu_count() or 1))
    try:
        total_percent = float(raw or 0.0)
    except Exception:
        total_percent = 0.0
    normalized = max(0.0, min(total_percent / float(cpu_count), 100.0))
    load_raw = _run_shell("sysctl -n vm.loadavg 2>/dev/null", timeout=1.0)
    load_match = re.findall(r"[-+]?\d+(?:\.\d+)?", load_raw)
    load_avg = [float(value) for value in load_match[:3]]
    return {
        "cpu_count": cpu_count,
        "total_percent": round(total_percent, 2),
        "normalized_percent": round(normalized, 2),
        "load_avg": load_avg,
    }


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _normalize_sensor_helper_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    fan_payload = dict(raw.get("fan") or {})
    temperature_payload = dict(raw.get("temperature") or {})
    fan_rpm = _coerce_float(
        fan_payload.get("rpm")
        if fan_payload
        else raw.get("fan_rpm")
    )
    temperature_celsius = _coerce_float(
        temperature_payload.get("celsius")
        if temperature_payload
        else raw.get("temperature_celsius", raw.get("cpu_temperature_celsius"))
    )
    return {
        "fan": {
            "rpm": round(fan_rpm, 2) if fan_rpm is not None else None,
            "available": fan_rpm is not None,
            "source": str(fan_payload.get("source") or raw.get("sensor_source") or "helper").strip() or "helper",
        },
        "temperature": {
            "celsius": round(temperature_celsius, 2) if temperature_celsius is not None else None,
            "available": temperature_celsius is not None,
            "source": str(temperature_payload.get("source") or raw.get("sensor_source") or "helper").strip() or "helper",
        },
    }


def _parse_sensor_helper_snapshot() -> Dict[str, Any]:
    command = str(os.environ.get("STRATA_HOST_SENSOR_COMMAND") or "").strip()
    if not command:
        return {}
    raw = _run_shell(command, timeout=2.5)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {
            "fan": {
                "rpm": None,
                "available": False,
                "source": "helper",
                "error": "Invalid JSON from STRATA_HOST_SENSOR_COMMAND",
            },
            "temperature": {
                "celsius": None,
                "available": False,
                "source": "helper",
            },
        }
    if not isinstance(payload, dict):
        return {}
    return _normalize_sensor_helper_snapshot(payload)


def _parse_ismc_snapshot() -> Dict[str, Any]:
    if not shutil.which("ismc"):
        return {}
    fans_raw = _run_shell("ismc fans --output json 2>/dev/null", timeout=2.5)
    temp_raw = _run_shell("ismc temp --output json 2>/dev/null", timeout=2.5)
    try:
        fans_payload = json.loads(fans_raw) if fans_raw else {}
    except Exception:
        fans_payload = {}
    try:
        temp_payload = json.loads(temp_raw) if temp_raw else {}
    except Exception:
        temp_payload = {}
    fan_rpm = None
    if isinstance(fans_payload, dict):
        candidates = []
        for name, item in fans_payload.items():
            if not isinstance(item, dict):
                continue
            label = str(name or item.get("key") or "").strip().lower()
            if "current speed" not in label:
                continue
            candidate = _coerce_float(item.get("quantity"))
            if candidate is not None:
                candidates.append(candidate)
        if candidates:
            fan_rpm = max(candidates)
    temperature_celsius = None
    if isinstance(temp_payload, dict):
        named_candidates = []
        fallback_candidates = []
        for name, item in temp_payload.items():
            if not isinstance(item, dict):
                continue
            candidate = _coerce_float(item.get("quantity"))
            if candidate is None:
                continue
            label = str(name or item.get("key") or "").strip().lower()
            if any(token in label for token in ("cpu", "soc", "die", "package", "performance")):
                named_candidates.append(candidate)
            else:
                fallback_candidates.append(candidate)
        if named_candidates:
            temperature_celsius = max(named_candidates)
        elif fallback_candidates:
            temperature_celsius = max(fallback_candidates)
    if fan_rpm is None and temperature_celsius is None:
        return {}
    return {
        "fan": {
            "rpm": round(fan_rpm, 2) if fan_rpm is not None else None,
            "available": fan_rpm is not None,
            "source": "ismc",
        },
        "temperature": {
            "celsius": round(temperature_celsius, 2) if temperature_celsius is not None else None,
            "available": temperature_celsius is not None,
            "source": "ismc",
        },
    }


def get_host_telemetry_snapshot(*, max_age_s: float = 5.0) -> Dict[str, Any]:
    now = time.monotonic()
    cached_at = float(_HOST_CACHE.get("captured_at") or 0.0)
    if now - cached_at <= max(0.0, float(max_age_s)) and _HOST_CACHE.get("snapshot"):
        return dict(_HOST_CACHE["snapshot"])

    memory_free_pct = _parse_memory_free_pct()
    thermal = _parse_thermal_status()
    cpu = _parse_cpu_usage()
    helper_snapshot = _parse_sensor_helper_snapshot()
    if not helper_snapshot:
        helper_snapshot = _parse_ismc_snapshot()
    snapshot = {
        "captured_at": time.time(),
        "cpu": cpu,
        "memory": {
            "free_percent": memory_free_pct,
            "pressure": (
                "high" if memory_free_pct is not None and memory_free_pct < 15
                else "moderate" if memory_free_pct is not None and memory_free_pct < 30
                else "nominal" if memory_free_pct is not None
                else "unknown"
            ),
        },
        "thermal": thermal,
        "fan": {
            "rpm": None,
            "available": False,
        },
        "temperature": {
            "celsius": None,
            "available": False,
        },
    }
    if helper_snapshot:
        snapshot["fan"].update(dict(helper_snapshot.get("fan") or {}))
        snapshot["temperature"].update(dict(helper_snapshot.get("temperature") or {}))
    _HOST_CACHE["captured_at"] = now
    _HOST_CACHE["snapshot"] = snapshot
    return dict(snapshot)
