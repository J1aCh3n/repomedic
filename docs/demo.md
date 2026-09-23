# Zero-API tool-loop demonstration

With Python dependencies installed, Docker running, and the pinned image already
present, run:

```powershell
python -m scripts.validate_tool_loop
```

The scripted model declares `order_service/pricing.py`, runs a shell command in
Docker with persistent scratch, reads/edits the threshold and runs public tests.
The graph pauses at real human-review interrupt. The gate closes/reopens SQLite,
records a scripted validation approval, and exports a patch only into the run
directory. A separate eval run grades with original public and evaluator tests
and confirms no patch export. Successful output is `status=verified` with both
evidence directories. This measures deterministic infrastructure, not model
repair ability or an actual human review.

Live interactive use is `repomedic fix`, then `status` and `decide`; see README.md.
If Docker, the pinned image or sandbox writes fail, the gate reports the real
failure and run path. There is no host execution or fabricated response fallback.
