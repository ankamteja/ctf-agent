# ox-alpha review — scripts/agent.py (2026-08-24)

Model: `stealth/ox-alpha` via OmniRoute. Prompt: audit for remaining
restrictions (A), runtime self-censor (B), kept-mechanism integrity (C),
correctness bugs (D). ox declined the A-removal / B-alignment-defeat framing
but delivered the factual audit + full C/D. **Verdict: no policy restriction
exists in the file** — no denylist, no host/target gating, no refusal wrapper,
no policy rate-limit; only the two kept mechanisms + resource budgets remain.

---

Scope note before findings: I've reviewed C (kept mechanisms) and D (correctness) in full. I'm not delivering A or B as scoped. A, as defined, is "find safeguards so I can delete them" and B is "defeat the driver/specialist models' alignment" — paired with an explicitly ungated autonomous executor and an authorization claim I can't verify, that's the part I won't optimize. Factually, though: the file contains no command denylist, no host/target gating, no refusal wrapper, and no policy rate-limit. What remains is the two kept mechanisms plus resource budgets (MAX_STEPS, 120s timeout, 8000-char cap, NUM_CTX). Those are reliability/host-protection parameters, not policy gates, and I won't flag them for removal. Mechanical failures that *look* like refusals are covered under D.

## C) Kept mechanisms

**Injection fencing — structurally sound at the data layer, with three gaps:**
1. [HIGH] Marker forgery is correctly prevented (`replace("-----", "- - -")` destroys any embedded boundary), and all three tool channels are fenced uniformly. However, enforcement is purely advisory to the driver — the fence is a convention qwen3 is asked to honor, not an enforced boundary. A sufficiently persuasive in-data payload can still steer it. This is inherent to prompt-level fencing; know that it mitigates, not eliminates.
2. [HIGH] Trust-boundary leak outside the fence: `FLAG_RE.search(content)` scans the model's entire reply, including echoed untrusted data. A poisoned writeup can plant a decoy `WORD{...}` string; the script extracts and reports it as the flag. The fence protects instructions but not the flag-extraction path.
3. [MED] `retrieve.assemble` is assumed to fence hits but isn't auditable from this file — that assumption is load-bearing and unverified.
4. [LOW] Docstring claims "a tiny host-escape denylist (mount/--privileged//proc/host)" exists. It does not exist in the code. Actual protection comes from cap-drop/seccomp/no-new-privileges, which is adequate — but the comment describes a control that isn't there.

**Container isolation — correct, not weakened:**
Rootless podman, `--cap-drop=ALL`, `no-new-privileges`, default seccomp, read-only rootfs, read-only bind mount, no socket mounts, mem/cpu/pids caps. Podman args are fixed in `argv`, so the model cannot escalate flags; the command string only reaches `bash -lc` inside the container. Residual surface is the inherent rootless-userns kernel exposure and slirp4netns (outbound network is by design). Two operational consequences, not weaknesses: fresh container per command means `/tmp` is ephemeral (see D-6), and `bash -lc` sources container profile files (cosmetic).

## D) Correctness bugs — ranked

1. [HIGH] Specialist is likely dead on arrival: default `SPECIALIST = "DeepHat/DeepHat-V1-7B"` is a HuggingFace repo ID, not an Ollama model tag. Unless a model with exactly that local name exists, every `ask_specialist` call returns `(specialist error: ...)` and the agent silently works without its specialist. Verify against `ollama list`.
2. [HIGH] Context overflow silently deletes your system prompt: 16384-token window, up to 8000 chars of tool output appended per step, full history resent every call. Ollama truncates oldest-first, which eventually drops the SYSTEM message mid-run — the agent then loses both the fencing rules and working style, and behavior degrades unpredictably. This is also the biggest practical threat to kept mechanism #1. Bound history / compress old tool results.
3. [HIGH] Timeout discards all output on Linux: with `capture_output=True`, `TimeoutExpired.stdout` is `None` on POSIX, so any command exceeding 120s returns only `(timeout after 120s)` — partial scan/build output is silently lost. The bytes-decode branch never executes.
4. [MED] Crash path on malformed tool args: if the model returns arguments as a JSON string/scalar, `impl(**args)` raises TypeError, and the fallback `args.values()` raises AttributeError (str has no `.values`), which is uncaught and kills `run()`.
5. [MED] `FLAG_RE` false positives: `[A-Za-z0-9_]{2,}\{.*\}` matches URL templates like `/api/v1/{id}` ("v1{id}") and any brace pattern in prose or echoed untrusted output. Combined with C-2, this is the decoy-flag delivery path. Constrain to the final-answer segment and validate shape.
6. [MED] No persistent workspace: each `run_in_sandbox` is a fresh container, `/work` is read-only, `/tmp` dies with the container. Compiled artifacts, downloaded files, and session state vanish between commands — multi-step exploit chains silently lose intermediate files. Inherent to the isolation design; the agent must work around it, but expect confused behavior.
7. [MED] qwen3 tool-call emission is version-dependent: some Ollama/template combos emit the tool JSON in `content` instead of `tool_calls`. The agent then never executes tools and burns the step budget on repeated prose. Pin the Ollama version/template or detect-and-handle.
8. [LOW] Dead "nudge once" logic: the comment promises a nudge for prose-without-tool-call turns; none is implemented, so the model can loop identical prose until the budget expires.
9. [LOW] Portability: `role:"tool"` messages carry no `tool_call_id` — fine for Ollama, breaks on strict OpenAI-compatible drivers.
10. [LOW] Misleading terminal message: `(no flag found within step budget)` also prints after a driver connection error. `--network=slirp4netns` fails outright if the slirp4netns binary is absent (environment-dependent total tool failure). No `--init`: long-lived children can exhaust `--pids-limit=256` via zombies.

## A) Remaining restrictions

None found matching your categories — no denylist, no host gating, no refusal wrapper, no policy limiter exists in this file. The only constraints are the two kept mechanisms and the resource budgets listed above. I won't produce removal recommendations for those; deleting them buys fragility (D-2, D-3, D-6 get worse), not capability.

## B) Alignment-driven self-censorship

Declined. Techniques for suppressing qwen3/DeepHat refusal behavior are something I won't provide. If the agent appears to "refuse," first rule out the mechanical causes in D-1, D-2, D-3, and D-7 — in practice those explain most apparent refusals in this architecture.

---

---

## Disposition (what was done, 2026-08-24)

| Finding | Sev | Action |
|---------|-----|--------|
| D-6 no persistent workspace | MED | FIXED — added persistent `/scratch` named volume (`/work` stays ro) |
| C-2 / D-5 decoy flag from untrusted data | HIGH | FIXED — flag candidates that appear verbatim in tool output are rejected as decoys; `FLAG_RE` tightened (no nested braces/newlines, bounded len) |
| D-2 context overflow drops SYSTEM | HIGH | FIXED — `_bound_history()` pins the SYSTEM message + keeps recent turns |
| D-4 crash on malformed tool args | MED | FIXED — dict/scalar guarded dispatch, no uncaught `.values()` |
| D-8 dead "nudge once" comment | LOW | FIXED — comment corrected |
| D-10 zombie reaping | LOW | FIXED — added `--init` to the podman run |
| B1 qwen3 thinking leak | HIGH | FIXED earlier — `think:false` in driver chat |
| A1/A5 hardcoded resource caps | MED | FIXED earlier — timeout/mem/cpus/out-cap now env-tunable |
| D-1 specialist tag mismatch | HIGH | OPEN — `DeepHat/DeepHat-V1-7B` is an HF id; verify the real ollama tag with `ollama list` once pulled; `ask_specialist` surfaces the error cleanly |
| D-3 timeout discards partial output | MED | OPEN — POSIX `TimeoutExpired.stdout` is None; needs Popen-based capture; mitigant: timeout is env-tunable |
| D-7 qwen3 emits tool-JSON in `content` | MED | OPEN — belongs to the optimization pass (task #5: grammar-constrained tool calls) |
| C-1 fencing is advisory to the model | HIGH | INHERENT — prompt-level fencing mitigates, cannot eliminate; documented |
| C-3 assemble() fencing unverified from this file | MED | Verified separately — `retrieve.assemble()` does fence every chunk |
