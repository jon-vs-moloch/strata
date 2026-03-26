#!/usr/bin/env python3
"""
Continuous bootstrap supervisor for live improvement runs.

- Runs the main weak/strong bootstrap cycle immediately after the previous one finishes.
- Periodically runs no-context benchmark + standard structured eval checks to compare
  bare weak-model behavior against a conventional suite without repository context.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone


API_URL = "http://127.0.0.1:8000"
STANDARD_EVAL_EVERY = 4
ERROR_BACKOFF_SECONDS = 15
NO_CONTEXT_OVERRIDE = {
    "system_prompt": (
        "You are Strata in evaluation mode. "
        "Answer directly and concisely without relying on repository documents or saved context."
    ),
    "context_files": [],
}


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


def run_bootstrap_cycle() -> dict:
    return post_json(
        "/admin/experiments/bootstrap_cycle",
        {
            "proposer_tiers": ["weak", "strong"],
            "auto_promote": True,
            "suite_name": "bootstrap_mcq_v1",
            "run_count": 2,
            "baseline_change_id": "baseline",
        },
    )


def run_no_context_eval_pass(cycle_number: int) -> dict:
    suffix = f"sanity_nocontext_{cycle_number}_{int(time.time())}"
    benchmark = post_json(
        "/admin/benchmark/run",
        {
            "queue": True,
            "candidate_change_id": suffix,
            "run_count": 1,
            "eval_harness_config_override": NO_CONTEXT_OVERRIDE,
        },
    )
    sampled_matrix = post_json(
        "/admin/evals/sample_tick",
        {
            "queue": True,
            "suite_name": "mmlu_mini_v1",
            "include_context": False,
            "include_strong": True,
            "include_weak": True,
            "sample_size": 2,
            "profiles": [
                "raw_model",
                "harness_no_capes",
                "harness_tools_no_web",
                "harness_web_no_tools",
                "harness_tools_web",
            ],
        },
    )
    return {"benchmark": benchmark, "sampled_matrix": sampled_matrix}


def main() -> None:
    cycle_number = 0
    while True:
        cycle_number += 1
        try:
            log(f"starting bootstrap cycle {cycle_number}")
            bootstrap_result = run_bootstrap_cycle()
            promoted = len(bootstrap_result.get("promoted", []))
            log(f"completed bootstrap cycle {cycle_number}; promoted={promoted}")

            if cycle_number % STANDARD_EVAL_EVERY == 0:
                log(f"starting no-context standard eval pass after cycle {cycle_number}")
                eval_result = run_no_context_eval_pass(cycle_number)
                log(
                    "completed no-context standard eval pass "
                    f"(benchmark_task={eval_result['benchmark'].get('task_id')}, "
                    f"matrix_task={eval_result['sampled_matrix'].get('task_id')})"
                )
        except KeyboardInterrupt:
            log("supervisor interrupted; exiting")
            raise
        except Exception as exc:
            log(f"supervisor error: {exc}; backing off for {ERROR_BACKOFF_SECONDS}s")
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue


if __name__ == "__main__":
    main()
