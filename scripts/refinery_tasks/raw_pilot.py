"""Raw single-call baseline for refinery_tasks_v1 coding tasks.
Usage: python raw_pilot.py <repo_dir> <endpoint> <out.jsonl> [--reps N] [--temperature T] [--ids a,b]
For each task: one chat call with the task prompt (task_prompt()), take the last fenced code
block (or the whole reply), write it to <tmp>/<target_file>, run the task's pytest file there."""
import argparse, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time
import httpx

def task_prompt(repo, t):
    test_src = (pathlib.Path(repo) / t["test_file"]).read_text()
    return (f"{t['title']}\n\n{t['description']}\n\nWrite the complete contents of `{t['target_file']}`. "
            f"It must pass this pytest file (`{t['test_file']}`):\n\n```python\n{test_src}```\n\n"
            "Reply with the complete Python module in a single ```python code block.")

def extract_code(text):
    # Same rule as Strata's EvaluationPipeline._check_syntax: first ```python block, else first ``` block, else all.
    text = text or ""
    if "```python" in text:
        return text.split("```python", 1)[1].split("```", 1)[0]
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0]
    return text

def run_tests(repo, t, code, timeout=60):
    with tempfile.TemporaryDirectory() as td:
        target = pathlib.Path(td) / t["target_file"]; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code)
        test = pathlib.Path(td) / t["test_file"]; test.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(pathlib.Path(repo) / t["test_file"], test)
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test)], cwd=td,
                               capture_output=True, text=True, timeout=timeout,
                               env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": td})
            tail = (r.stdout.strip().splitlines() or [""])[-1]
            return r.returncode == 0, tail
        except subprocess.TimeoutExpired:
            return False, "timeout"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("repo"); ap.add_argument("endpoint"); ap.add_argument("out")
    ap.add_argument("--reps", type=int, default=1); ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--ids", default=""); ap.add_argument("--max-tokens", type=int, default=0)
    a = ap.parse_args()
    tasks = [json.loads(l) for l in (pathlib.Path(a.repo) / "strata/eval/suites/refinery_tasks_v1/tasks.jsonl").read_text().splitlines() if l.strip()]
    if a.ids:  # run in the order given
        by_id = {t["id"]: t for t in tasks}
        tasks = [by_id[i] for i in a.ids.split(",") if i in by_id]
    with open(a.out, "a") as fh:
        for rep in range(1, a.reps + 1):
            for t in tasks:
                body = {"model": "local", "temperature": a.temperature, "messages": [{"role": "user", "content": task_prompt(a.repo, t)}]}
                if a.max_tokens: body["max_tokens"] = a.max_tokens
                t0 = time.time()
                try:
                    r = httpx.post(a.endpoint, json=body, timeout=3600); r.raise_for_status(); j = r.json()
                    msg = j["choices"][0]["message"]; content = msg.get("content") or ""; usage = j.get("usage") or {}
                    finish = j["choices"][0].get("finish_reason"); err = ""
                except Exception as e:
                    content, usage, finish, err = "", {}, None, repr(e)
                wall = round(time.time() - t0, 1)
                ok, tail = run_tests(a.repo, t, extract_code(content)) if content else (False, "no content")
                rec = {"id": t["id"], "rep": rep, "temperature": a.temperature, "passed": ok, "pytest": tail, "wall_s": wall,
                       "usage": usage, "finish_reason": finish, "error": err, "response": content,
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                fh.write(json.dumps(rec) + "\n"); fh.flush()
                print(f"{rec['utc']} {t['id']} rep{rep} passed={ok} {tail} wall={wall}s tok={usage.get('completion_tokens')} finish={finish}", flush=True)

if __name__ == "__main__":
    main()
