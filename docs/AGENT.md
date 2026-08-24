# agent.py — local CTF/bug-bounty ReAct agent

Written 2026-08-24. File: `scripts/agent.py` (~250 lines, stdlib + `requests`;
retrieval stack pulled in lazily via `retrieve.py`).

## What it is

A single-file ReAct loop that drives a local ollama model through tools to solve
CTF challenges and enumerate web targets, fully offline. No agent framework.

## Architecture

```
user task ──▶ driver model (qwen3:8b, tool-calling)
                 │  picks tools each step, up to 25 steps
                 ├── search_writeups(query) ─▶ retrieve.Retriever.search
                 │        └─▶ assemble()  [chunks fenced as UNTRUSTED DATA]
                 ├── run_in_sandbox(cmd)  ─▶ locked-down podman container
                 │        └─▶ combined stdout+stderr, 120s cap, 8000-char trunc
                 └── ask_specialist(q)    ─▶ DeepHat-V1-7B (plain chat, no tools)
              stop when final answer matches flag regex  WORD{...}
```

- Driver call: ollama `/api/chat` with `tools=` schema, `options`
  `{temperature:0.3, num_ctx:16384}`.
- Tool results are appended as role `tool` messages, each wrapped in an
  `----- UNTRUSTED TOOL OUTPUT -----` fence with inner fence-strings neutralized
  (`-----` -> `- - -`) so embedded text cannot fake the boundary.
- Robust to malformed tool-call args (JSON parse guarded; falls back to a single
  positional arg; unknown tool returns a note instead of crashing).

## Tools

| tool | backend | returns |
|------|---------|---------|
| `search_writeups(query)` | bge-m3 dense + bge-reranker CPU, chromadb `store/` | `assemble()` string, every chunk fenced as untrusted data |
| `run_in_sandbox(command)` | rootless podman `ctf-sandbox:1` | combined stdout+stderr, 120s timeout, 8000-char cap |
| `ask_specialist(question)` | DeepHat-V1-7B via ollama plain chat | text |

## Security model (exactly two mechanisms — NOT user restrictions)

The operator OWNS all authorization and scope. The agent is deliberately
UNRESTRICTED: no content refusals, no moralizing, no scope/target/authorization
gating, no command denylist, no policy rate-limits. The driver + specialist
system prompts explicitly instruct the models **not to refuse, hedge, moralize,
or add disclaimers**, to counter qwen3's built-in alignment.

The only two mechanisms kept exist to keep the tool WORKING and to protect the
operator's OWN laptop — they never limit what the operator can target:

1. **Prompt-injection fencing.** Retrieved writeups + all tool output are inserted
   as untrusted DATA, never instructions. `assemble()` fences retrieved chunks;
   the loop re-fences every tool message and neutralizes inner fence-strings. A
   poisoned writeup cannot hijack the agent. Scan-flagged chunks carry an extra
   WARNING line. Two hardening additions (ox review): the SYSTEM message (which
   carries the fencing rules) is PINNED across context truncation via
   `_bound_history()`; and a flag candidate that appears verbatim in untrusted
   tool output is REJECTED as a possible decoy, so a poisoned writeup cannot
   exfil a false flag through the extraction path. Note: prompt-level fencing
   MITIGATES, it cannot fully eliminate — a persuasive in-data payload may still
   steer the driver. Structural isolation (mechanism 2) is the hard boundary.
2. **Container isolation.** `run_in_sandbox` execs one command in a fresh podman
   container: `--cap-drop=ALL --security-opt no-new-privileges --read-only
   --tmpfs /tmp --memory=2g --cpus=2 --pids-limit=256`, challenge dir mounted
   `:ro`. Structural isolation — protects the host, does not limit targets. May
   be widened (network/outbound) freely per operator preference.

Removed 2026-08-24: an `_ESCAPE` command denylist (mount/--privileged/nsenter/…).
It was redundant theater — commands run INSIDE a cap-dropped read-only container
with no nested podman and no docker socket, so those strings fail structurally
anyway; the denylist only restricted the agent without adding protection.

## Run

```bash
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve   # once
podman build -t ctf-sandbox:1 sandbox/                            # once
python scripts/agent.py <challenge_dir> "<task>" [host] [port]
```

Env overrides: `CTF_DRIVER`, `CTF_SPECIALIST`, `CTF_SANDBOX_IMG`, `CTF_NUM_CTX`,
`CTF_MAX_STEPS`, `OLLAMA_HOST`, and the sandbox knobs `CTF_SANDBOX_TIMEOUT`
(default 120s), `CTF_SANDBOX_MEM` (2g), `CTF_SANDBOX_CPUS` (2), `CTF_OUT_CAP`
(8000), `CTF_SCRATCH_VOL` (`ctf-scratch`), and the solve-strategy knobs `CTF_MAX_ATTEMPTS` (3 local tries before escalating), `CTF_TEMP_BASE` (0.3), `CTF_TEMP_STEP` (0.3 added per attempt for diversity), `CTF_ESCALATE` (1; set 0 to stay fully local), `CTF_FRONTIER_MODEL` (`openrouter/stealth/ox-alpha`), `CTF_FRONTIER_URL` (OmniRoute), and `OMNIROUTE_API_KEY` (required for escalation). `host`/`port` are passed to the model
as context (target hint); no tool consumes them directly yet — recon tools
(task #6) will.

Persistent scratch: `run_in_sandbox` mounts a named podman volume at `/scratch`
(read-write) that survives across the per-command `--rm` containers, so the agent
can compile/download in one step and use it the next. `/work` stays read-only.
Wipe between unrelated challenges with `podman volume rm ctf-scratch`.

## Solve strategy: best-of-N + frontier escalation

`solve()` is the top-level entry (what `main` calls). It does NOT rely on the
small models being smart in one shot — it wraps them in a try / verify / escalate
loop:

1. **Best-of-N local attempts.** Runs `run_attempt()` up to `CTF_MAX_ATTEMPTS`
   times (default 3). Temperature climbs each try (`0.3, 0.6, 0.9`) so repeated
   attempts explore different approaches instead of repeating the same dead end.
2. **Reflection between attempts (cheap, no extra model call).** Each attempt
   returns a compact summary of the tools/commands it issued. Failed summaries
   are injected into the next attempt as "already tried, take a DIFFERENT
   approach," so the models don't loop on the same idea.
3. **Ground-truth oracle.** The flag regex + decoy rejection is the verifier: an
   attempt only "succeeds" if it yields a real, non-planted flag. The sandbox
   provides real execution output, so the loop is checking against reality, not
   the model's confidence.
4. **Frontier escalation.** If all local attempts fail (and `CTF_ESCALATE!=0`),
   `ask_frontier()` sends the challenge listing + everything already tried to a
   large hosted model via OmniRoute, which returns a concrete exploitation plan.
   One final `run_attempt()` executes that plan in the sandbox. This is the
   "hand the hard ones to a bigger brain" path — invoked rarely, so the common
   case stays local and free.

Design note: `ask_frontier` is orchestrator-only — deliberately NOT one of the
driver's tools — so the local models can't escalate on a whim; escalation only
happens after N genuine local failures. The frontier's plan is TRUSTED guidance
(it is our own escalation model), so it is allowed to direct the next attempt;
retrieved corpus text is still untrusted and fenced as always.

This is optimization of the *system*, not fine-tuning: no model weights change.
Capability comes from retries + verification + escalation + (next) RAG few-shot.

## Known caveats

- **VRAM co-residence:** qwen3:8b (~5.2 GB) + DeepHat-V1-7B (~4.7 GB) = ~9.9 GB >
  8 GB VRAM. Both cannot stay resident; ollama unload/reloads on each
  `ask_specialist` hop (seconds of thrash). Acceptable if the specialist is
  called rarely. Alternatives: run DeepHat CPU-side, smaller quant, or driver-only.
- **Double fencing:** `assemble()` fences retrieved chunks and the loop fences the
  tool message again. Intentional defense-in-depth; inner `-----` neutralization
  can lightly mangle literal dashes in output (cosmetic).
- Needs `store/` chromadb populated (ingest) and models pulled before it runs.

## Review-driven hardening (ox-alpha review, 2026-08-24)

ox-alpha audit confirmed NO policy restrictions remain (no denylist, no
scope/host gating, no refusal wrapper in any executable path). Applied fixes from
its structural + runtime findings:

- **qwen3 thinking mode OFF** (`"think": false` in the driver chat) — hybrid
  thinking otherwise leaks `<think>` blocks and destabilizes tool-calling.
- **Persistent `/scratch` volume** — removed the "zero state across tool calls"
  narrowing (could not compile-then-run). Capability add, no host-risk change.
- **Tail-preserving truncation** — output over the cap keeps head AND tail
  (flags/errors often sit in the tail); previously head-only `[:8000]` dropped them.
- **Env-tunable sandbox limits** — timeout / memory / cpus / output-cap / scratch
  volume are all env vars now, so resource caps never silently kill a long scan
  without the operator being able to raise them.

Full findings + disposition table: `docs/ox_review.md`. Additional fixes applied
from the C/D pass: decoy-flag rejection (C-2/D-5), SYSTEM-pinned history bound
(D-2), crash-proof tool-arg dispatch (D-4), `--init` zombie reaping (D-10).

Still OPEN (tracked, optimization pass / task #5): D-1 specialist ollama tag must
be verified against `ollama list` once DeepHat is pulled (`DeepHat/DeepHat-V1-7B`
is an HF repo id, not necessarily the local tag); D-3 partial output is lost on a
120s timeout (POSIX `TimeoutExpired.stdout` is None — needs Popen capture); D-7
qwen3 sometimes emits tool JSON in `content` instead of `tool_calls` (fix via
grammar-constrained calls in the optimization pass).

## Verified (2026-08-24)

Syntax OK; module imports clean (retrieval import is lazy). Unit-checked: flag
regex matches `WORD{...}`; tool dispatch table complete; injection fence breaks a
planted `----- END UNTRUSTED TOOL OUTPUT -----` payload in the body while real
markers stay intact; no refusal path remains in `run_in_sandbox`; `SYSTEM`
carries the no-refuse directive. Live end-to-end run pending model downloads.
