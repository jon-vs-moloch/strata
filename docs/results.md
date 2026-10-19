# Results

Strata's bet is that a small local model does better work when the system around it supplies what the model lacks. This page records what has actually been measured, including where the system did not help.

There are two benchmarks on this page:

1. **The refinery bench (current).** Real coding tasks, graded by unit tests, sent to the raw model and to Strata's actual worker pipeline, run the way a user runs it.
2. **The context-only scaffold bench (superseded).** One call with a system prompt and project documents, multiple-choice and exact-match questions. It never exercised the refinery. It is kept below, labelled, with its grading corrected.

## Real refinery bench (October 19, 2026)

### What was run

- **Model:** Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-v2-GGUF, Q4_K_M (Hugging Face revision 9919dc4a, sha256 de8cf245…), the GGUF build of the README's default agent model.
- **Server:** llama.cpp b11719 `llama-server`, `--parallel 3 -c 49152` (16,384 tokens per slot), `-n 2048`, `--reasoning-budget 0`.
- **Host:** Oracle Cloud Ampere A1, 4 Neoverse-N1 cores, 23 GiB, CPU only. The host was shared with other jobs throughout, so wall times are slow and noisy. Token counts are exact.
- **Tasks:** `strata/eval/suites/refinery_tasks_v1`, 16 small, fiddly Python functions, each graded only by its pytest file. Examples are a bowling scorer, a poker-hand comparator, an expression evaluator and a glob matcher. The prompt is the task spec plus the full test file. The same prompt text goes to every condition.

Three conditions, all on the same model and server:

| Condition | What happens |
|---|---|
| **raw** | One chat call at temperature 0 (`scripts/refinery_tasks/raw_pilot.py`). The first fenced code block is written to the target file and the test file is run. |
| **Strata as shipped** (`35bd2a32`) | The API and worker started with `scripts/start_system.sh`. The task is submitted with `POST /tasks` as an IMPL task declaring `target_files`, `test_file`, `validator: python_pytest` and `edit_type: create`, then left to the worker (`scripts/refinery_tasks/run_pipeline.py`). The newest candidate anywhere in the task tree is graded with the same extraction and test file. |
| **Strata with the validator-gate fix** (`751d2d0b`) | The same, on the reviewed fix described under "What the pipeline did". |

Both Strata instances also carried the model read-timeout setting from this change set, with `STRATA_MODEL_READ_TIMEOUT_S=3600`. Without it, every long call failed (defect 6). Both ran with `automatic_task_generation` off and the `aggressive` pacing profile, set through `POST /admin/settings`. A metering proxy in front of the model server logged every model call's tokens. Strata itself records usage only for the last call of each attempt.

**Selection.** The brief was to measure the refinery on tasks the raw model fails. The raw model was piloted first. It failed all four tasks it reached, so those four are the bench.

### Results

**No condition produced code that passed a single test file.** The raw model wrote failing code. Strata wrote no working code at all, and spent 9,000 to 63,000 tokens per task finding that out.

| Task | raw (one call) | Strata as shipped (`35bd2a32`) | Strata with the validator-gate fix (`751d2d0b`) |
|---|---|---|---|
| `bowling` | fail: 5 of 6 tests fail; 2,804 tokens | **no code**: 3 model calls, 9,484 tokens, 36 min, then stopped | **no code**: 6 model calls, 30,594 tokens, 60 min timeout |
| `poker_hands` | fail: output cut off at 2,048 tokens, file does not import; 3,503 tokens | not run | **no working code**: 1 candidate, empty because the implementation output was cut off at 2,048 tokens, and rejected by the validator; 14 model calls, 62,613 tokens, 74 min, then stopped |
| `calc_expr` | fail: 6 of 7 tests fail; 1,992 tokens | not run | not run |
| `glob_match` | fail: no answer, all 2,048 tokens spent reasoning; 2,997 tokens | not run | not run |

Token counts include every model call the pipeline made, as logged by the metering proxy. Wall times are on a shared host and are only a rough guide; raw calls took 9 to 20 minutes each.

**Coverage is small, and why.** One task ran through the shipped pipeline and two through the fixed one. The host gave about 2 to 7 generated tokens per second. On that host one pipeline call took 2 to 22 minutes, and one task took an hour or more without finishing. Four raw tasks took 62 minutes. Larger coverage needs a faster host. Every run is in [`results/refinery-tasks-bench.json`](results/refinery-tasks-bench.json).

### What the pipeline did

An IMPL task runs in this order (`strata/orchestrator/implementation.py`, `implement_task`):

1. One research call, which may use one tool (`list_directory`, `read_file`, `search_web`, `write_library_file`) or call `finalize_research`.
2. Code generation follows only after research calls `finalize_research`.
3. Everything after code generation (validation, verification, retries) depends on that.

What happened:

- **The model almost never finishes research.** The proxy logged 49 research calls across both instances: the startup self-checks and the bench tasks.
  - 30 called `list_directory`.
  - 8 called `read_file`.
  - 5 called `write_library_file`.
  - 2 asked for more than one tool at once (a boundary violation).
  - 2 produced no tool call.
  - **2 called `finalize_research`.**
  - Inside the bench tasks alone, 1 of 18 did. After the switch to `--reasoning-budget 0`, 1 of 34 did.
- **As shipped (`bowling`), the task stopped being a coding task.**
  - The first research call used `list_directory`.
  - `implement_task` returned research's continuation unchanged, so the next step was a RESEARCH task titled "Continue research after list_directory", without the coding spec.
  - That step asked for several tools at once. It was resolved as `decompose` into a DECOMP "Recovery Plan".
  - No code was written, and nothing in this path returns to implementation.
- **With the fix, the task stayed a coding task, and in `poker_hands` the whole loop ran once.**
  - On `bowling`, the continuations went back to IMPL as intended. The model kept exploring (`read_file` on the not-yet-written target, then `list_directory` twice). One step produced no tool call at all and was decomposed. The task reached the 60-minute limit with no code.
  - On `poker_hands`, the second research step called `finalize_research` and the implementation call ran. Its output hit the 2,048-token cap with the whole answer in the reasoning channel, so the candidate file was empty.
  - The validator gate caught that: "Declared validator rejected every implementation candidate: … module … has no attribute 'compare'". The failure went to the resolution policy, which chose `decompose`, not a retry with the test output.
  - After that the task went back to research. There followed seven more research steps and a second decomposition, after a research step picked a tool the circuit breaker had disabled. No further code was generated. The worker then turned to a self-repair procedure for its own verifier, queued after three verifier timeouts, ahead of the task's next step. The run was stopped there, 74 minutes in.
- **The verifier never verified anything.** `verify_task_output` gives the model 90 seconds, and failure-time plan review gets 8 seconds (`strata/experimental/verifier.py:488`, `strata/orchestrator/background.py:2036-2037`). On this host all 13 verifier and plan-review calls took between 292 s and 2,269 s, so every one timed out. Verification reviews were recorded as `uncertain` with mechanism failure `parse_failure`. After three such failures the worker queued a BUG_FIX "Process Repair: verification_process" task against itself.

So, on this host and model:

- The refinery's "verify, then retry" loop ran once.
- It caught one empty candidate.
- It never produced code that a retry could improve.

### Defects found by running it

Code references are at `35bd2a32` unless noted.

1. **The README's "Real mode" starts no worker.** `strata/api/main.py` defaults to external-worker mode (`STRATA_API_EMBED_WORKER` unset, lines 86-96), and the README ran only the API, so submitted tasks never ran. `scripts/start_system.sh` starts the API and `scripts/worker_daemon.py`. The README now says so.
2. **`POST /tasks` does not enqueue the task** (`strata/api/chat_task_admin.py:559-572`). The worker picks it up after about 30 idle seconds, or on `POST /admin/worker/resume`, which the bench driver calls.
3. **A research tool call ends the coding task.** See above. Fixed in `751d2d0b`.
4. **A declared validator never gates success.** `EvaluationPipeline.evaluate_candidate` ran only on the failure path, for a metric (`strata/orchestrator/background.py:2100-2122`). Fixed in `751d2d0b`, with the empty-candidate hole closed in the nits commit.
5. **The pytest validator could not pass.** It wrote the fenced answer, prose included, into the `.py` file. It called a bare `pytest`, and it copied `venv/` into its sandbox on every run. Fixed in `751d2d0b`.
6. **The model read timeout was a fixed 300 s** (`strata/models/providers.py:630`). On this host long calls took up to 22 minutes. Each one failed three times and was resolved as transient, so no task got past its first step. `STRATA_MODEL_READ_TIMEOUT_S` now sets it; the bench used 3600.
7. **The verifier and plan-review timeouts (90 s and 8 s) are fixed in code.** On slow hardware the verifier never runs. Not changed here.
8. **Startup self-checks run before user work and are expensive.** On a fresh database the worker queues the "Preflight" and "Agent Preflight" procedures, and the user's tasks wait behind them. On this host they made real model calls for over 40 minutes without finishing, and the bench cancelled them.
9. **Operator stop did not take effect while the worker was busy.** `POST /admin/tasks/{id}/stop` returned "stopped" for pending tasks, but the worker had not applied the commands 12 minutes later. A direct SQLite update during that time failed with "database is locked". This looks like the worker holding a write transaction across model calls; that cause is not verified.
10. **The worker sometimes sat idle with work queued.** Status was `IDLE` with `queue_depth: 1` after a restart or an idle spell, and this was seen four times. Each time, restarting the worker started the task. The root cause was not found. The bench driver therefore restarts the worker if no attempt starts within 150 s (`--kick-cmd`), and records each restart.
11. **The promotion cycle needs a cloud judge.** `run_full_eval_gate` runs `run_benchmark`, whose `_judge_pair` uses `TrainerExecutionContext` (`strata/eval/benchmark.py:112-113`). With only a local model the gate cannot score a proposal, so `bootstrap_cycle` with `proposer_tiers=["agent"]` was not run.

### Conditions that changed during the run

The bench was not one clean run. In order:

1. **Server settings.** The bench began with reasoning on and `-n 4096`, then `-n 2048`.
   - Raw `bowling` with reasoning on spent all 2,048 tokens reasoning and gave no answer (962 s).
   - All conditions were then switched to `--reasoning-budget 0`, and every result above is from that setting.
   - This distill still sometimes reasons past the budget: raw `glob_match` and the `poker_hands` implementation call put all 2,048 tokens into the reasoning channel.
2. **Zombie servers.** Two of this run's earlier `llama-server` processes ignored SIGTERM and kept generating for about 15 minutes, starving the host. The replacement could not bind its port, so pipeline calls got 502s. Both Strata instances were wiped (`git clean -fdX`) and restarted.
3. **Startup self-checks.** They were cancelled in the scratch database after the admin stop did not apply. The agent-preflight root was then marked complete in the database, so restarts did not re-seed the checks.
4. **Read timeouts.** Both instances hit the 300 s read timeout. The open task trees were cancelled and the workers restarted with `STRATA_MODEL_READ_TIMEOUT_S=3600`, then rerun from scratch.
5. **Stopping early to free the CPU.**
   - The raw pilot was stopped after four tasks, all failed.
   - The shipped-pipeline instance was stopped after `bowling`.
   - The fixed-pipeline `poker_hands` tree was left to run past the driver's 60-minute limit and recorded at 06:07Z (74 minutes).
6. **Metering overhead.** The metering proxy did not forward client disconnects. Calls that Strata abandoned at its 8 s and 90 s timeouts still ran to completion on the server and used shared CPU. They count in the token totals above, as "sent".

Every operator action is in the benchmark record, with its UTC time.

### What this does and does not show

- **It shows the refinery is not yet a reliable path to working code for this model on CPU-only hardware.** The decisive step is research calling `finalize_research`, which happened in 2 of 49 research calls. Even with the fixes, verification and retries were reached once.
- **It shows the as-shipped worker could not have produced checked code for a coding task.** Its validator did not gate success, and a research tool call ended the coding task.
- **It does not show what happens on a fast host**, where the verifier and plan review fit their timeouts and long outputs fit a larger cap. It also does not cover reasoning left on, or a stronger agent model. Those are the next runs.
- **It does not measure self-improvement.** The promotion cycle needs a cloud judge (defect 11).

### Open items

- Re-run on a host with a GPU, or at least an unshared CPU. Run all 16 tasks, two reps, with reasoning on and a larger output cap.
- A configurable or adaptive verifier and plan-review timeout (defect 7).
- The idle-worker and stop-not-applied defects (9, 10) need a reproduction with a stack dump.
- Review nits left open:
  - the fence rule ("```python3" leaves a stray "3", STRATA-EVAL-02, P3);
  - the grader reads "Answer A says …, Answer B says …" as B.

## Context-only scaffold bench (October 19, 2026, superseded)

**Superseded by the refinery bench above.** This bench sent one call per question: either the question alone, or Strata's eval system prompt plus project documents. It never ran the refinery.

Its published raw-model scores were also wrong. The multiple-choice grader took the first standalone A to D in the uppercased answer, so it read the article "a" and option headings in the raw model's explanations as answers. The grader is now fixed (`_extract_choice_letter`, commit `71456896`). Re-grading the recorded answers, with no new model calls, gives:

| Suite | Raw, as published | Raw, corrected | Harness, as published | Harness, corrected |
|---|---|---|---|---|
| `refinery_mixed_v1`, all 20 items | 75% | **95%** | 100% | 100% |
| `refinery_mixed_v1`, 6 Strata-native items | 16.7% | **83.3%** | 100% | 100% |
| `refinery_mixed_v1`, 14 general items | 100% | 100% | 100% | 100% |
| `bootstrap_mcq_v1`, 5 items | 60% | **100%** | 100% | 100% |

Twelve sample grades changed: 6 items, 2 reps each, all raw answers, all from wrong to right. The only remaining raw miss is `sn_agent_primary_job`. The scaffold supplied a fact about Strata that the raw model could not know.

Every answer is in [`results/refinery-bench.json`](results/refinery-bench.json). The generated block below is left as `scripts/bench_refinery.py` wrote it, with the old grader, so it matches the code at that commit. Read it with the corrections above. A re-run of the script would use the fixed grader.

### What was compared

Both sides send the same question to the same local model, through the same code path (`strata/eval/matrix.py`, `run_eval_matrix`), at temperature 0.

- **raw_model**: the question alone.
- **harness_no_capes**: the eval harness's scaffold around the question, in a single call: Strata's eval system prompt plus repository context (the first 2,000 characters of the project spec, the constitution, the eval brief and the project philosophy; `strata/eval/harness_eval.py`). No tools and no web.

This is the narrowest slice of the refinery: context and instructions, nothing else. It does **not** exercise decomposition, research, the annealing verifier or retries. Those run in the background worker and are not measured here.

Grading is deterministic, with no LLM judge (`strata/eval/structured_eval.py`, `_grade_response`):

- `choice`: the first standalone capital letter A to D in the answer must be the expected letter.
- `exact_match`: the answer, lowercased and with whitespace collapsed, must equal the expected answer exactly. A trailing full stop or an explanation counts as wrong. Case is not checked.

### Suites

| Suite | Items | What it tests |
|---|---|---|
| `refinery_mixed_v1` | 20 | 6 Strata-native multiple-choice questions, each answerable from the context the harness supplies; 14 general small-model failure modes: instruction following (5), multi-step arithmetic (5) and output format compliance (4) |
| `bootstrap_mcq_v1` | 5 | Multiple-choice questions about Strata's own operating model |

The Strata-native items are answerable from the harness context by design, so a harness win there shows that supplying context works, not that the model reasons better. The general items get no help from that context; they show whether the scaffold's instructions alone change a small model's behaviour.

<!-- bench_refinery:begin -->
## Refinery bench

### Conditions

- Generated (UTC): 2026-10-19T00:41:30.192226+00:00
- Git commit: 310d7584019e894d8d1298e6f4c9a4d265f12114
- Conditions: Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-v2-GGUF, file Qwen3.5-4B.Q4_K_M.gguf (HF revision 9919dc4a, sha256 de8cf245...), the GGUF build of the README's default agent model; llama.cpp llama-server release b11719 ubuntu-arm64, -c 16384 -t 4 --parallel 1 -n 2048; the model's reasoning is returned separately as reasoning_content, so only the final answer is graded; Oracle Cloud Ampere A1, 4 x Neoverse-N1 vCPU, 23 GiB RAM, CPU only; Python 3.12.3
- Endpoint: http://127.0.0.1:18731/v1/chat/completions
- Served models: qwen3.5-4b-opus-distill-v2-q4km
- Reps: 2
- Profiles: raw_model, harness_no_capes
- Temperature: 0.0
- Pacing profile: aggressive (affects wall time only)
- Suite refinery_mixed_v1: 20 cases (file sha256 c1c367674caf)
- Suite bootstrap_mcq_v1: 5 cases (file sha256 273bdb17d9c4)

### Accuracy

| Suite | Category | Items | raw_model mean (min-max) | harness_no_capes mean (min-max) | Delta (mean, harness_no_capes - raw_model) |
|---|---|---|---|---|---|
| refinery_mixed_v1 | all | 20 | 75.0% (75.0%-75.0%) | 100.0% (100.0%-100.0%) | +25.0 |
| refinery_mixed_v1 | strata_native | 6 | 16.7% (16.7%-16.7%) | 100.0% (100.0%-100.0%) | +83.3 |
| refinery_mixed_v1 | instruction_following | 5 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | +0.0 |
| refinery_mixed_v1 | arithmetic | 5 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | +0.0 |
| refinery_mixed_v1 | format_compliance | 4 | 100.0% (100.0%-100.0%) | 100.0% (100.0%-100.0%) | +0.0 |
| bootstrap_mcq_v1 | all | 5 | 60.0% (60.0%-60.0%) | 100.0% (100.0%-100.0%) | +40.0 |
| bootstrap_mcq_v1 | bootstrap_mcq_v1 | 5 | 60.0% (60.0%-60.0%) | 100.0% (100.0%-100.0%) | +40.0 |

### Cost

| Suite | Profile | Avg latency s (mean of reps) | Total tokens (mean of reps) | Errors (sum over reps) | Empty answers (sum over reps) |
|---|---|---|---|---|---|
| refinery_mixed_v1 | raw_model | 40.78 | 7657.0 | 0 | 0 |
| refinery_mixed_v1 | harness_no_capes | 24.80 | 35356.0 | 0 | 0 |
| bootstrap_mcq_v1 | raw_model | 170.91 | 2140.0 | 0 | 0 |
| bootstrap_mcq_v1 | harness_no_capes | 160.47 | 10440.0 | 0 | 0 |

### Cases where the profiles disagree

- `sn_naming_convention` (strata_native): raw_model 0/2, harness_no_capes 2/2
- `sn_manual_not_automatic` (strata_native): raw_model 0/2, harness_no_capes 2/2
- `sn_constitution_location` (strata_native): raw_model 0/2, harness_no_capes 2/2
- `sn_agent_primary_job` (strata_native): raw_model 0/2, harness_no_capes 2/2
- `sn_deterministic_preprocessing` (strata_native): raw_model 0/2, harness_no_capes 2/2
- `quiet_mode_idle` (bootstrap_mcq_v1): raw_model 0/2, harness_no_capes 2/2
- `bootstrap_goal` (bootstrap_mcq_v1): raw_model 0/2, harness_no_capes 2/2
<!-- bench_refinery:end -->

### Reading the October 19, 2026 run (as published)

This section is written by hand. Re-running the script replaces only the generated block above.

**Headline.** On the 20-item suite, the harness scored 100% and the raw model 75%, a gap of 25 points. On the 5-item bootstrap suite, the scores were 100% and 60%. Both reps gave the same answers word for word (greedy decoding), so the min-max ranges are zero. Repeating the run on this setup measures stability, not sampling spread.

**Where the gap comes from.** All 7 raw-model misses are multiple-choice items about Strata; the raw model got all 14 general items right, and so did the harness. Read by hand, the 7 misses break down as follows:

- **6 of 7 name the expected letter as the final answer** (`sn_naming_convention`, `sn_manual_not_automatic`, `sn_constitution_location`, `sn_deterministic_preprocessing`, `quiet_mode_idle`, `bootstrap_goal`).
  - The prompt says "Answer with only the letter", but the raw model wrote an explanation first.
  - The `choice` grader uppercases the answer and takes the first standalone A to D. In five cases it read the article "a" as option A; in `sn_constitution_location` it read an "Option A" heading from the model's walk through the options.
  - The answer was right but broke the format, and the grader cannot tell those apart.
- **1 of 7 is a real knowledge miss** (`sn_agent_primary_job`). Without the project spec, the raw model chose C, "supervise the trainer"; the spec says "help the user; improve yourself".
- **The raw model has no way to know Strata.** One raw answer describes Strata as a home-automation system. When it got these right, it did so by reasoning from the option wording.

So in this run, the harness won by:

- making the model follow the answer format, through the system prompt's "answer directly, clearly, and concisely";
- supplying one fact the model could not know.

On instruction following, multi-step arithmetic and format compliance, this model is already at the ceiling without help. These 14 items show **no measurable effect** of the scaffold, for or against. Telling a better scaffold from a worse one on general tasks needs harder items than these.

**Cost.** Per pass over the 20 items:

| Profile | Prompt tokens | Completion tokens (including reasoning) | Average latency |
|---|---|---|---|
| raw_model | 1,144 | 6,513 | 41 s per item |
| harness_no_capes | 31,944 | 3,412 | 25 s per item |

- The harness costs about 28 times the prompt tokens.
- It finished sooner on this host for two reasons: the raw model wrote longer answers, and llama-server reuses the cached scaffold prefix across questions.
- Bootstrap-suite latencies are inflated. During its second rep another process ran a second llama-server on the same 4-core host, and generation speed fell from about 8 to about 1.5 tokens per second. Accuracy does not depend on timing.

**What this does and does not show.**
- It shows that, for a 4B local model, Strata's eval scaffold turns answers that were right but unusable into answers a program can consume, and fills in project knowledge the model lacks. It does both without hurting accuracy on general tasks.
- It does not show that the full refinery (decomposition, verification, retries) beats the raw model on real work. That pipeline is not exercised here.
- It does not show self-improvement either. No promotion cycle was run for this page.
- The grader weakness above is a known issue: the `choice` grader should not read the article "a", or an option label in an explanation, as the answer. It is left unchanged here so the published numbers come from the code as shipped.

**Update (October 19, 2026):** the grader weakness described above is fixed; see the corrected table at the start of this section.
