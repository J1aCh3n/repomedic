from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs
import json
import secrets

from repomedic.agent_graph import AgentGraphRunner, AgentRunResult
from repomedic.agent_schemas import ApprovalDecision


def render_control_panel(result: AgentRunResult, *, csrf_token: str) -> str:
    status = escape(result.status)
    summary = ""
    edits = ""
    if result.proposal:
        summary = escape(str(result.proposal.get("summary", "")))
        edits = escape(json.dumps(result.proposal.get("edits", []), indent=2))
    diff = escape(result.proposal_diff or "No proposed diff.")
    error = escape(result.error or "")
    decision_form = ""
    if result.awaiting_approval:
        decision_form = f"""
        <form method="post" action="/decision">
          <input type="hidden" name="csrf" value="{escape(csrf_token)}">
          <label for="feedback">Feedback for revise/reject</label>
          <textarea id="feedback" name="feedback" maxlength="4000"></textarea>
          <div class="buttons">
            <button name="action" value="approve" class="approve">Approve and continue</button>
            <button name="action" value="revise">Request revision</button>
            <button name="action" value="reject" class="reject">Reject run</button>
          </div>
        </form>"""
    pipeline = "".join(
        f'<span class="node">{name}</span><span class="arrow">→</span>'
        for name in ("Planner", "Investigator", "Coder", "Approval", "Tests")
    ) + '<span class="node">Reviewer</span>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RepoMedic {escape(result.run_id)}</title>
<style>
body {{ font: 15px system-ui; max-width: 1100px; margin: 32px auto; padding: 0 20px;
       color: #17212b; background: #f5f7fa; }}
h1 {{ margin-bottom: 4px; }} .status {{ display:inline-block; padding:6px 10px;
border-radius:999px; background:#dce8ff; font-weight:700; }}
.pipeline {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:24px 0; }}
.node {{ background:white; border:1px solid #ccd5df; padding:8px 12px; border-radius:8px; }}
.arrow {{ color:#667; }} section {{ background:white; border:1px solid #d8dee6;
border-radius:10px; padding:18px; margin:14px 0; }} pre {{ overflow:auto; background:#111827;
color:#e5e7eb; padding:14px; border-radius:8px; white-space:pre-wrap; }}
textarea {{ width:100%; min-height:80px; box-sizing:border-box; margin:8px 0 12px; }}
.buttons {{ display:flex; gap:10px; flex-wrap:wrap; }} button {{ padding:9px 13px; cursor:pointer; }}
.approve {{ background:#16794b; color:white; border:0; }} .reject {{ background:#a52828; color:white; border:0; }}
.error {{ color:#a52828; }}
</style></head><body>
<h1>RepoMedic control panel</h1>
<div>Case <code>{escape(result.case_id)}</code> · Run <code>{escape(result.run_id)}</code></div>
<p class="status">{status}</p>
<div class="pipeline">{pipeline}</div>
<section><h2>Proposal</h2><p>{summary or "No active proposal."}</p><pre>{edits or "[]"}</pre></section>
<section><h2>Diff preview</h2><pre>{diff}</pre></section>
{f'<section><h2 class="error">Error</h2><p>{error}</p></section>' if error else ''}
{decision_form}
</body></html>"""


def serve_control_panel(
    runner: AgentGraphRunner,
    *,
    run_id: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    on_result: Callable[[AgentRunResult], None] | None = None,
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("the control panel may only bind to the local machine")
    csrf_token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path != "/":
                self._send(404, "Not found")
                return
            self._send(
                200,
                render_control_panel(runner.inspect(run_id), csrf_token=csrf_token),
            )

        def do_POST(self) -> None:
            if self.path != "/decision":
                self._send(404, "Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, "Invalid request")
                return
            if length > 16_000:
                self._send(413, "Request too large")
                return
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            if fields.get("csrf", [""])[0] != csrf_token:
                self._send(403, "Invalid CSRF token")
                return
            try:
                decision = ApprovalDecision(
                    action=fields.get("action", [""])[0],
                    feedback=fields.get("feedback", [""])[0],
                )
                result = runner.resume(run_id, decision)
            except Exception as error:
                self._send(500, f"<h1>Run failed</h1><pre>{escape(str(error))}</pre>")
                return
            if on_result:
                on_result(result)
            self._send(200, render_control_panel(result, csrf_token=csrf_token))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer((host, port), Handler)
    print(f"RepoMedic control panel: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
