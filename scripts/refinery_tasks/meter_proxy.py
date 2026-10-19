"""Metering proxy: listens on 127.0.0.1:<listen>, forwards to 127.0.0.1:<upstream>, and appends one JSON
line per request (UTC start/end, path, status, usage, finish_reason, prompt sha) to <log>. Measurement only:
request and response bodies pass through unchanged. Usage: python meter_proxy.py <listen> <upstream> <log>"""
import hashlib, json, sys, time, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import httpx

LISTEN, UPSTREAM, LOG = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
lock = threading.Lock()
client = httpx.Client(timeout=7200)

def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _fwd(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        t0, s0 = time.time(), utc()
        hdrs = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        try:
            r = client.request(method, f"http://127.0.0.1:{UPSTREAM}{self.path}", content=body, headers=hdrs)
            status, data, ctype = r.status_code, r.content, r.headers.get("content-type", "application/json")
        except Exception as e:
            status, data, ctype = 502, json.dumps({"error": repr(e)}).encode(), "application/json"
        rec = {"start": s0, "end": utc(), "wall_s": round(time.time() - t0, 2), "method": method, "path": self.path, "status": status}
        if method == "POST":
            try:
                req = json.loads(body or b"{}")
                rec["req_sha"] = hashlib.sha256(body).hexdigest()[:16]
                rec["n_messages"] = len(req.get("messages") or [])
                rec["tools"] = len(req.get("tools") or [])
                rec["temperature"] = req.get("temperature"); rec["max_tokens"] = req.get("max_tokens")
                rec["first_system_head"] = str(((req.get("messages") or [{}])[0] or {}).get("content") or "")[:160]
                j = json.loads(data)
                rec["usage"] = j.get("usage")
                ch = (j.get("choices") or [{}])[0]
                rec["finish_reason"] = ch.get("finish_reason")
                rec["tool_calls"] = [((c or {}).get("function") or {}).get("name") for c in ((ch.get("message") or {}).get("tool_calls") or [])]
            except Exception as e:
                rec["parse_error"] = repr(e)[:200]
        with lock, open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.send_response(status)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self): self._fwd("GET")
    def do_POST(self): self._fwd("POST")

ThreadingHTTPServer(("127.0.0.1", LISTEN), H).serve_forever()
