#!/usr/bin/env python3
"""
Continuous bootstrap supervisor for live improvement runs.

- Runs the main weak/strong bootstrap cycle immediately after the previous one finishes.
- Periodically runs no-context benchmark + standard structured eval checks to compare
  bare weak-model behavior against a conventional suite without repository context.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


API_URL = "http://127.0.0.1:8000"
SUPERVISOR_MODE = os.getenv("SUPERVISOR_MODE", "continuous").strip().lower()
CONTINUOUS_TELEMETRY_EVERY = 3
CONTEXT_SNAPSHOT_EVERY = 12
ERROR_BACKOFF_SECONDS = 15
POLL_INTERVAL_SECONDS = 5
JOB_TIMEOUT_SECONDS = 60 * 60
CONTINUOUS_BOOTSTRAP_PROPOSER_TIERS = ["strong"]
TELEMETRY_PROFILES = [
    "raw_model",
    "harness_no_capes",
    "harness_tools_no_web",
    "harness_web_no_tools",
    "harness_tools_web",
]


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def post_json(path: str, payload: dict, timeout: int = 900) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def get_json(path: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(f"{API_URL}{path}", method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_for_job(task_id: str, timeout: int = JOB_TIMEOUT_SECONDS) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = get_json(f"/admin/evals/jobs/{task_id}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            remaining = max(0, int(deadline - time.time()))
            log(
                f"job poll for {task_id} hit transient error ({exc}); "
                f"retrying in {POLL_INTERVAL_SECONDS}s with {remaining}s remaining"
            )
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        task = payload.get("task") or {}
        state = str(task.get("state") or "").lower()
        if state in {"complete", "cancelled", "abandoned"}:
            return task
        if state == "blocked":
            raise RuntimeError(f"queued job {task_id} blocked: {task.get('system_job_result')}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"queued job {task_id} did not finish within {timeout}s")


def run_bootstrap_cycle(*, cycle_number: int | None = None, lean: bool = False) -> dict:
    proposer_tiers = ["weak", "strong"]
    run_count = 2
    if lean:
        # Normal supervised operation is strong -> weak: the strong tier proposes
        # harness changes intended to improve the weak tier's performance.
        proposer_tiers = list(CONTINUOUS_BOOTSTRAP_PROPOSER_TIERS)
        run_count = 1
    return post_json(
        "/admin/experiments/bootstrap_cycle",
        {
            "proposer_tiers": proposer_tiers,
            "auto_promote": True,
            "suite_name": "bootstrap_mcq_v1",
            "run_count": run_count,
            "baseline_change_id": "baseline",
            "queue": True,
        },
    )


def run_sample_tick(*, include_context: bool, sample_size: int = 2) -> dict:
    return post_json(
        "/admin/evals/sample_tick",
        {
            "queue": True,
            "suite_name": "mmlu_mini_v1",
            "include_context": include_context,
            "include_strong": True,
            "include_weak": True,
            "sample_size": sample_size,
            "profiles": TELEMETRY_PROFILES,
        },
    )


def run_telemetry_cycle(cycle_number: int) -> dict:
    sampled_matrix = run_sample_tick(include_context=False, sample_size=2)
    return {
        "sampled_matrix": wait_for_job(str(sampled_matrix.get("task_id"))),
    }


def run_context_snapshot(cycle_number: int) -> dict:
    queued = run_sample_tick(include_context=True, sample_size=2)
    return {"sampled_matrix": wait_for_job(str(queued.get("task_id")))}


def _log_matrix_result(prefix: str, task: dict) -> None:
    result = ((task.get("system_job_result") or {}).get("result") or {})
    variants = result.get("variants") or []
    weak_variants = [variant for variant in variants if str(variant.get("mode")) == "weak"]
    strong_variants = [variant for variant in variants if str(variant.get("mode")) == "strong"]
    weak_avg = (
        sum(float(variant.get("accuracy", 0.0) or 0.0) for variant in weak_variants) / len(weak_variants)
        if weak_variants
        else 0.0
    )
    strong_avg = (
        sum(float(variant.get("accuracy", 0.0) or 0.0) for variant in strong_variants) / len(strong_variants)
        if strong_variants
        else 0.0
    )
    log(
        f"{prefix} task={task.get('task_id')} weak_avg={weak_avg:.2f} "
        f"strong_avg={strong_avg:.2f} variants={len(variants)}"
    )


def main() -> None:
    cycle_number = 0
    while True:
        cycle_number += 1
        try:
            if SUPERVISOR_MODE == "bootstrap":
                log(f"starting bootstrap cycle {cycle_number}")
                queued = run_bootstrap_cycle(cycle_number=cycle_number, lean=False)
                bootstrap_task = wait_for_job(str(queued.get("task_id")))
                bootstrap_result = ((bootstrap_task.get("system_job_result") or {}).get("result") or {})
                promoted = len(bootstrap_result.get("promoted", []))
                log(f"completed bootstrap cycle {cycle_number}; promoted={promoted}")
            elif SUPERVISOR_MODE == "telemetry_safe":
                log(f"starting telemetry supervision cycle {cycle_number}")
                eval_result = run_telemetry_cycle(cycle_number)
                matrix_task = eval_result["sampled_matrix"]
                _log_matrix_result(f"completed telemetry supervision cycle {cycle_number}", matrix_task)

                if cycle_number % CONTEXT_SNAPSHOT_EVERY == 0:
                    log(f"starting context-on snapshot after cycle {cycle_number}")
                    context_result = run_context_snapshot(cycle_number)
                    context_task = context_result["sampled_matrix"]
                    _log_matrix_result("completed context-on snapshot", context_task)
            else:
                log(f"starting continuous self-improvement cycle {cycle_number}")
                queued = run_bootstrap_cycle(cycle_number=cycle_number, lean=True)
                bootstrap_task = wait_for_job(str(queued.get("task_id")))
                bootstrap_result = ((bootstrap_task.get("system_job_result") or {}).get("result") or {})
                promoted = len(bootstrap_result.get("promoted", []))
                log(f"completed bootstrap phase {cycle_number}; promoted={promoted}")

                if cycle_number % CONTINUOUS_TELEMETRY_EVERY == 0:
                    eval_result = run_telemetry_cycle(cycle_number)
                    matrix_task = eval_result["sampled_matrix"]
                    _log_matrix_result(f"completed telemetry phase {cycle_number}", matrix_task)

                if cycle_number % CONTEXT_SNAPSHOT_EVERY == 0:
                    log(f"starting context-on snapshot after cycle {cycle_number}")
                    context_result = run_context_snapshot(cycle_number)
                    context_task = context_result["sampled_matrix"]
                    _log_matrix_result("completed context-on snapshot", context_task)
        except KeyboardInterrupt:
            log("supervisor interrupted; exiting")
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, KeyError, ValueError) as exc:
            log(f"supervisor error: {exc}; backing off for {ERROR_BACKOFF_SECONDS}s")
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue


if __name__ == "__main__":
    main()
