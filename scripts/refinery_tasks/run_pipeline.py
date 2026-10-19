"""Drive Strata's real worker pipeline on refinery_tasks_v1, one task at a time, the way a user would:
POST /tasks (type IMPL, constraints target_files/test_file/validator) to the running API, then let the
running worker daemon do research -> implementation -> verification -> resolution/retries.
Grades the latest candidate Strata produced anywhere in the task tree by running the task's pytest file.
Usage (from the Strata repo root, API + worker already running, metering proxy log given):
  python run_pipeline.py <out.jsonl> <proxy_log> --rep N [--ids a,b] [--timeout-min 45]"""
import argparse, json, os, pathlib, sqlite3, sys, time
import httpx
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from raw_pilot import task_prompt, extract_code, run_tests  # same prompt text, extraction and grader as raw

API = os.environ.get("STRATA_BENCH_API", "http://127.0.0.1:8000")  # a second instance can listen elsewhere
DB = "strata/runtime/strata.db"
LIVE = {"pending", "working", "PENDING", "WORKING"}

def utc(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def db():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30); c.row_factory = sqlite3.Row; return c

def tree(root):
    with db() as c:
        ids, frontier = [root], [root]
        while frontier:
            q = ",".join("?" * len(frontier))
            kids = [r["task_id"] for r in c.execute(f"select task_id from tasks where parent_task_id in ({q})", frontier)]
            ids += kids; frontier = kids
        q = ",".join("?" * len(ids))
        tasks = [dict(r) for r in c.execute(f"select task_id,parent_task_id,title,type,state,constraints,created_at,updated_at from tasks where task_id in ({q}) order by created_at", ids)]
        atts = [dict(r) for r in c.execute(f"select attempt_id,task_id,started_at,ended_at,outcome,reason,resolution,artifacts,evidence from attempts where task_id in ({q}) order by started_at", ids)]
        cands = [dict(r) for r in c.execute(f"select candidate_id,task_id,content_path,created_at from candidates where task_id in ({q}) order by created_at", ids)]
    return tasks, atts, cands

def live_agent_tasks(tasks):
    out = []
    for t in tasks:
        cons = json.loads(t["constraints"] or "{}")
        lane = str(cons.get("lane") or "agent"); pool = str(cons.get("work_pool") or "local_agent")
        # Only the local agent pool runs here; trainer/remote_agent work waits forever without cloud keys.
        if t["state"] in LIVE and lane != "trainer" and pool == "local_agent":
            out.append(t)
    return out

def any_live_agent_work():
    with db() as c:
        rows = [dict(r) for r in c.execute("select task_id,state,constraints from tasks where state in ('PENDING','WORKING','pending','working')")]
    return live_agent_tasks([{**r} for r in rows])

def proxy_calls(log, start, end):
    out = []
    if not os.path.exists(log): return out
    for line in open(log):
        try: r = json.loads(line)
        except Exception: continue
        if r.get("method") == "POST" and start <= r["start"] <= end: out.append(r)
    return out

def last_proxy_end(log):
    last = ""
    if os.path.exists(log):
        for line in open(log):
            try: last = max(last, json.loads(line).get("end", ""))
            except Exception: pass
    return last

def settle(root, log, timeout_s, quiet_s=90):
    t0 = time.time(); stable_since = None
    while time.time() - t0 < timeout_s:
        tasks, _, _ = tree(root)
        live = live_agent_tasks(tasks)
        lp = last_proxy_end(log)
        if not live:
            stable_since = stable_since or time.time()
            idle_for = time.time() - max(stable_since, _epoch(lp))
            if time.time() - stable_since >= quiet_s and idle_for >= 60:
                return "settled", round(time.time() - t0, 1)
        else:
            stable_since = None
        time.sleep(15)
    return "timeout", round(time.time() - t0, 1)

def _epoch(s):
    if not s: return 0.0
    import calendar
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("proxy_log")
    ap.add_argument("--rep", type=int, default=1); ap.add_argument("--ids", default=""); ap.add_argument("--timeout-min", type=float, default=45)
    ap.add_argument("--raw-gate", default="", help="raw_pilot JSONL; run only tasks whose raw result failed")
    ap.add_argument("--kick-cmd", default="", help="shell command that restarts the worker if no attempt starts")
    ap.add_argument("--kick-after-s", type=float, default=150)
    a = ap.parse_args()
    repo = os.getcwd()
    token = pathlib.Path("strata/runtime/api-token").read_text().strip()
    H = {"X-Strata-Token": token}
    tasks = [json.loads(l) for l in pathlib.Path("strata/eval/suites/refinery_tasks_v1/tasks.jsonl").read_text().splitlines() if l.strip()]
    if a.ids:  # run in the order given
        by_id = {t["id"]: t for t in tasks}
        tasks = [by_id[i] for i in a.ids.split(",") if i in by_id]
    for t in tasks:
        if a.raw_gate:
            # Selection rule: only tasks the raw model failed. Wait for the raw result, skip raw passes.
            raw = None
            while raw is None:
                for line in (open(a.raw_gate).read().splitlines() if os.path.exists(a.raw_gate) else []):
                    r = json.loads(line)
                    if r["id"] == t["id"]: raw = r
                if raw is None: time.sleep(30)
            if raw["passed"]:
                print(f"{utc()} {t['id']} skipped: raw passed", flush=True); continue
        # Start each task only when the agent pool has no live work (preflight, earlier trees).
        w0 = time.time()
        while any_live_agent_work() and time.time() - w0 < 1800: time.sleep(15)
        start = utc(); t0 = time.time()
        body = {"title": t["title"], "description": task_prompt(repo, t), "type": "IMPL", "lane": "agent",
                "constraints": {"lane": "agent", "work_pool": "local_agent", "target_files": [t["target_file"]],
                                "test_file": t["test_file"], "validator": "python_pytest", "edit_type": "create"}}
        r = httpx.post(f"{API}/tasks", json=body, headers=H, timeout=60); r.raise_for_status(); root = r.json()["id"]
        httpx.post(f"{API}/admin/worker/resume", params={"work_pool": "local_agent"}, headers=H, timeout=60)
        kicked = 0
        if a.kick_cmd:
            # Observed on Cedar: after an idle spell the worker can sit IDLE with the task queued. If no attempt
            # has started within kick_after_s, restart the worker with the given command (recorded in the output).
            k0 = time.time()
            while time.time() - k0 < a.kick_after_s and not tree(root)[1]:
                time.sleep(10)
            if not tree(root)[1]:
                os.system(a.kick_cmd); kicked = 1
        status, waited = settle(root, a.proxy_log, a.timeout_min * 60)
        if status == "timeout":
            ts, _, _ = tree(root)
            for x in live_agent_tasks(ts):
                try: httpx.post(f"{API}/admin/tasks/{x['task_id']}/stop", headers=H, timeout=60)
                except Exception: pass
        end = utc()
        ts, atts, cands = tree(root)
        graded = []
        for c in cands:
            p = pathlib.Path(c["content_path"])
            content = p.read_text() if p.exists() else ""
            if content:
                ok, tail = run_tests(repo, t, extract_code(content))
            else:
                # The October 19 run reported both cases as "missing candidate file".
                ok, tail = False, ("empty candidate file" if p.exists() else "missing candidate file")
            graded.append({"candidate_id": c["candidate_id"], "task_id": c["task_id"], "created_at": c["created_at"], "passed": ok, "pytest": tail, "content": content})
        calls = proxy_calls(a.proxy_log, start, end)
        usage = lambda k: sum(int((x.get("usage") or {}).get(k) or 0) for x in calls)
        att_rows = []
        for x in atts:
            art = json.loads(x["artifacts"] or "{}"); ev = json.loads(x["evidence"] or "{}")
            att_rows.append({"task_id": x["task_id"], "started_at": x["started_at"], "ended_at": x["ended_at"], "outcome": x["outcome"],
                             "resolution": x["resolution"], "reason": (x["reason"] or "")[:600], "failure_kind": ev.get("failure_kind"),
                             "verifier": art.get("verifier"), "duration_s": art.get("duration_s")})
        rec = {"id": t["id"], "rep": a.rep, "root_task_id": root, "start": start, "end": end, "wall_s": round(time.time() - t0, 1),
               "settle": status, "worker_restarts": kicked, "final_passed": bool(graded and graded[-1]["passed"]), "any_passed": any(g["passed"] for g in graded),
               "n_candidates": len(graded), "candidates": graded,
               "tasks": [{k: v for k, v in x.items() if k != "constraints"} for x in ts], "attempts": att_rows,
               "model_calls": len(calls), "prompt_tokens": usage("prompt_tokens"), "completion_tokens": usage("completion_tokens"),
               "total_tokens": usage("total_tokens"), "finish_length": sum(1 for x in calls if x.get("finish_reason") == "length")}
        with open(a.out, "a") as fh: fh.write(json.dumps(rec) + "\n")
        print(f"{end} {t['id']} rep{a.rep} settle={status} final={rec['final_passed']} any={rec['any_passed']} cands={len(graded)} "
              f"tasks={len(ts)} attempts={len(att_rows)} calls={len(calls)} tok={rec['total_tokens']} wall={rec['wall_s']}s", flush=True)

if __name__ == "__main__":
    main()
