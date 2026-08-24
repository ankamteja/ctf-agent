# TODO — where the project stands and what's next

Beginner-friendly checklist. This is the durable copy of the task list (the
in-chat one disappears when the chat is cleared). Updated 2026-08-24.

Read `docs/LEARN.md` first if the terms here are unfamiliar.

## Done ✅

- [x] **Retrieval stack** — `scan_corpus.py` (safety scan), `ingest.py` (build the
      searchable index), `retrieve.py` (search + inject-safe fencing).
- [x] **Sandbox image** — `ctf-sandbox:1` built (the locked-down room where
      commands run).
- [x] **Agent loop** — `scripts/agent.py`: the driver model + 3 tools, with
      injection fencing and no restrictions on the model.
- [x] **Best-of-N + frontier escalation** — tries locally several times, then
      hands hard challenges to a big model. (See `docs/AGENT.md` Solve strategy.)
- [x] **qwen3:8b** (driver model) — downloaded.
- [x] **Embeddings** — `bge-m3` + `bge-reranker-v2-m3` — downloaded (cached).

## Done ✅ (cont., 2026-08-25)

- [x] **Corpus download** — hacktricks, p4-ctf, perfectblue, gtfobins, payloads
      all landed. `ctf-archives` DROPPED on purpose (not a failure to retry):
      it's raw CTF *problems* (binaries + prompt files, no writeups/solutions),
      so it has zero value for RAG and its huge binaries would've bloated
      ingest for nothing. The scan/chunk/embed pipeline only wants prose
      writeups with worked solutions.
- [x] **scan_corpus.py run on final corpus** — 3481 files: 3016 allow / 464
      flag / 1 quarantine. Flags are expected+correct (hacktricks AI-prompts,
      SSRF/CSP docs legitimately contain injection-pattern text) — not a bug.
- [x] **ingest.py moved to GPU** — `device="cuda" if torch.cuda.is_available()`
      (was hardcoded CPU). This is safe ONLY because ingest is a one-off batch
      job that never runs concurrently with the agent — ollama is idle during
      ingest so the GPU is free. `retrieve.py` (used LIVE during agent solving,
      alongside the loaded driver model) stays CPU-only on purpose: bge-m3 +
      reranker + qwen3:8b together would be ~8.3 GB, too tight against 8 GB
      VRAM. Do not "fix" retrieve.py to match — that would reintroduce the
      VRAM contention the CPU choice exists to avoid.
- [x] **Baseline E2E challenge built** — `challenges/baseline01/`: a
      self-written ret2win (`chall.c`, no PIE/canary/stack-protector,
      `read()` stack overflow, offset 72, `win()` reads `flag.txt`). Verified
      locally with pwntools before handing to the agent: exploit prints
      `flag{baseline_smoke_ok}`. Chosen (ox-alpha's recommendation, cross-
      checked) over a picoCTF-style crypto warmup because it forces the full
      tool path (run_in_sandbox: inspect → offset → craft payload → execute)
      instead of being solvable in-context with zero tool calls.

## 2026-08-25: REAL exploitation attempt — honest result: 0/1

After fixing the flag-in-comment leak (below) and re-running clean:
**qwen3:8b alone did not solve it in 3 attempts. The frontier-guided 4th
attempt also did not solve it.** This is the first honest local+frontier
solve-rate data point on an actual (not trivially-readable) challenge, and
it's a real 0, not a design failure to paper over.

**What actually happened, concretely (all genuine capability signal, not
infra bugs -- verified each one by hand):**
- The model correctly diagnosed the vulnerability (buffer overflow in
  `vuln()`, 200-byte read into a 64-byte buffer) and correctly identified
  that `win()` needs to be reached and decodes an XOR-obfuscated flag. That
  reasoning was right every single time, across all 4 attempts.
- It never constructed a real ret2win payload (offset + packed `win()`
  address via pwntools-style `p64`). Its first instinct was a NOP-sled guess
  with no address at all -- structurally incapable of working regardless of
  offset.
- It pivoted to trying to call `win()` directly via gdb
  (`call (void) win()`) instead of completing the actual overflow. This
  *can* work in principle, but hit a real, subtle pitfall: the breakpoint it
  set fires before `setvbuf(stdout,...)` runs, so `win()`'s `puts()` output
  sits in a stdio buffer that's never flushed before gdb tears the process
  down -- silently swallowed, zero output, zero error. Verified by hand:
  `gdb -batch ... -ex 'call (void)win()'` produces no `$N =` line at all,
  while `-ex 'print 1'` right after it does. This is a genuine technique gap,
  not a bug in this repo.
- **Strong, repeated pattern worth acting on**: across every attempt, once a
  gdb invocation stopped producing new information, the model re-issued the
  *exact same command* 10-20 times in a row rather than diagnosing why or
  trying something different (even when the frontier's plan explicitly
  handed it a corrected, working command form). The repeat-guard
  deliberately excludes `run_in_sandbox` (correctly -- state can differ
  between calls), but there's currently NO mechanism that warns the model
  when a command produced literally the same output as last time. Candidate
  fix, backed by this real data now (not speculation): track a
  same-output-N-times-in-a-row counter per attempt and inject a ledger note
  ("this exact command produced identical output last N times -- it is not
  giving you new information, try something structurally different") when it
  fires. Not implemented yet -- added to the roadmap below.

**Two more real bugs found and fixed via this run** (in addition to the two
from the previous entry):
- `run_in_sandbox` used `subprocess.run(..., text=True)`, which decodes
  stdout/stderr as strict UTF-8. A successfully-exploited binary's real
  output often contains raw non-UTF-8 bytes (leaked pointers) -- this threw
  `UnicodeDecodeError`, caught generically, and returned as a `(sandbox
  error: ...)` string that the model then tried to search the corpus for.
  ox-alpha inferred this purely from the model's own search query in the run
  log, without ever seeing this function. Fixed: capture as bytes, decode
  permissively.
- `challenges/baseline01/chall.c` had the plaintext flag sitting directly in
  a C comment documenting the XOR scheme -- self-inflicted; frontier
  escalation spotted it immediately and told the agent to just read the
  source instead of exploiting anything. Removed.

## 2026-08-25: FIRST REAL RUN (trivial version) — solved, but be honest about what it proves

`python scripts/agent.py ./challenges/baseline01 "find the flag"` — SOLVED,
3 steps, attempt 1, exit 0: `ls -la /work` -> `cat /work/flag.txt` -> flag.
Real, clean, end-to-end. Took 3 real infra bugs to get here (see below), not
zero — the design work held up, the environment didn't.

**What actually broke (in order, each found by RUNNING it, not reviewing it):**
1. The task message told the model the HOST challenge_dir path; files are
   mounted at `/work` inside the sandbox. A concrete wrong path beat the tool
   schema's correct-but-abstract "/work" mention. Burned all 25 steps of
   attempt 1. Fixed: message only ever says `/work` now, no host path shown.
2. `run_in_sandbox` used `--network=slirp4netns`, not installed on this host
   -- podman failed at container-setup, EVERY call, before the command even
   ran. The model spent 3 full attempts reacting to that error text as if it
   were real output. **This exact failure mode was already flagged [LOW] in
   an earlier review (`docs/ox_review.md`) and sat unfixed until it broke
   first contact with reality** -- the lesson: a predicted "low severity,
   environment-dependent" issue is still exactly what breaks run #1. Fixed:
   `--network=pasta` (installed, podman's modern replacement).
3. Unrelated to the agent: `ollama serve` died silently between sessions:
   the agent blocked forever on a dead connection with zero output. Restarted
   it. Watch for this whenever a run produces no output at all.

**The honest caveat**: `challenges/baseline01` mounts the whole directory
read-only, including `flag.txt` sitting right next to the binary. The agent
solved it by reading the file directly -- it never exploited the buffer
overflow. This proves the FULL PLUMBING works end-to-end for the first time
(tool-calling round-trip, sandbox networking, path handling, the corrected
flag-source check, solve-memory write-back) but proves NOTHING about actual
exploitation capability, which was the whole point of picking a pwn
challenge over a trivial one. Asked ox-alpha what to do about this with the
real run log attached (not a changelog) -- see their answer before deciding
whether to fix this challenge or move straight to a small varied eval set.

## 2026-08-25 round: agent.py rewrite + ox-alpha brutal review

A full round of fixes/features went into `agent.py` this session (turn-aware
truncation, tool_call_id, malformed-JSON feedback, a KV-cache-friendly state
ledger, a blocker-report gate, a corrected flag-acceptance check, a
repeat-guard + stall detector, category-conditioned few-shots, a forced
pwn/crypto specialist consult, richer frontier-escalation evidence, a
DeepHat-vs-loopback ablation flag, and a human-in-the-loop failure handoff
file). Full detail + reasoning: `docs/HANDOFF.md` 2026-08-25 section — don't
duplicate it here.

**ox-alpha rated the whole project 6/10, brutally, on request** — "8 as a
design doc, 3-4 as a validated system." The single biggest finding: none of
this has been run for real yet. That review's own 7→8→9→10 framework is now
the actual roadmap (copied into HANDOFF.md). The items below are reordered to
match it.

## Next, in order (ox-alpha's 7→8→9→10 framework) 📋

0. [ ] **First end-to-end run = get a baseline.** Still not done. Corpus is
   ingesting now (5889 files incl. newly-added google-ctf). Once done:
   ```bash
   python scripts/agent.py ./challenges/baseline01 "find the flag"
   ```
   This is a plumbing smoke test (self-written ret2win), not a capability
   measurement — see item 3 below for why that distinction matters.
1. [ ] **Verify num_ctx=16384 actually fits.** Never empirically measured
   against qwen3's real VRAM footprint + `OLLAMA_KV_CACHE_TYPE=q8_0` headroom.
   Silent context truncation would quietly break the ledger/few-shot/history
   management built this session without ever throwing an error.
2. [ ] **Categorize the first 10-20 runs' failures mechanically** (truncation?
   timeout? malformed JSON despite the feedback loop? genuine reasoning
   failure?) BEFORE touching agent logic again. Don't tune blind — said
   repeatedly by ox-alpha this session, still the operative rule.
3. [ ] **Build a small graded eval set** (a handful of challenges spanning
   categories — not just the one ret2win) with automated scoring, so future
   changes have a real solve-rate number to move instead of vibes. This is
   what "8/10" requires per ox-alpha's framework; it doesn't exist yet.
4. [ ] **Run the ablations the feature flags were built for**: `CTF_FEWSHOT=0`,
   `CTF_FORCE_SPECIALIST=0`, `CTF_SPECIALIST_MODE=loopback` (does DeepHat earn
   its swap cost over qwen3 alone?) — all exist in code, none have been
   exercised against real data yet.
5. [ ] **Exemplar-with-code retrieval (RAG quality).** Keep exploit code blocks
   intact when chunking; retrieve SOLVED write-ups *with their working
   scripts*, not just prose (google-ctf's addition this session was aimed at
   this). Consider a code-aware embedder.
6. [ ] **Injection safety test.** Plant a fake instruction in a write-up;
   confirm the agent treats it as data and doesn't obey it.
7. [x] **Solve-memory (compounding).** DONE — every solve is written back to
   `corpus/solved/` as a trusted exemplar (see `agent.py:_record_solution`).
8. [~] **Constrained tool-calls.** PARTIAL — malformed JSON now gets fed back
   as an explicit parse error instead of silently defaulting to `{}` (this
   session). True grammar-constrained decoding (ollama `format` + native
   `tools` together) was not attempted — uncertain if ollama supports
   combining them cleanly; revisit only if the parse-error feedback loop
   turns out insufficient once real failure data exists (see item 2).
9. [ ] **Category playbooks + recon tools.** pwn/crypto/web/rev/forensics need
   different tool sets; a generic pipeline underperforms. Add web-enum recon
   tools (subfinder/httpx/nuclei/ffuf; use `~/go/bin/httpx`, NOT the miniforge
   python package of the same name). Also: **the bug-bounty half of the
   stated goal has zero infrastructure right now** (no HTTP client, no proxy,
   no scope-awareness) — either build it or explicitly descope the README/
   goal statement to "CTF now, bug-bounty later," per ox-alpha's review.

## Optional / decisions 🔧

- **DeepHat specialist model — downloaded and running.** DeepHat-V1-7B is a
  strong uncensored, security-tuned 7B — a good LOCAL specialist for
  CTF/exploit work. Using the quantized GGUF that fits 8GB:
  `hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q4_K_M` (~4.7 GB). Can't sit in VRAM
  at the same time as qwen3 (5.2 + 4.7 > 8 GB) — DECIDED 2026-08-25: both stay
  on GPU, ollama swaps per `ask_specialist` call, cost accepted (not CPU-pinned).
  DeepHat's actual value over qwen3 alone is still an UNMEASURED ASSUMPTION —
  `CTF_SPECIALIST_MODE=loopback` routes ask_specialist through qwen3 itself
  instead, for exactly this ablation, once real eval data exists (see item 4
  above). If DeepHat is absent, `ask_specialist` returns a clean error and the
  agent keeps working on qwen3 + escalation.
- **Frontier model for escalation** — defaults to `openrouter/stealth/ox-alpha`.
  For a stronger backstop, set `CTF_FRONTIER_MODEL` to a bigger model via
  OmniRoute (needs `OMNIROUTE_API_KEY`). No code change needed.

## How to check background progress anytime

```bash
ollama list                     # which models are downloaded
du -sh ~/.cache/huggingface     # embeddings download size
tail logs/finish.log            # corpus download progress
ls corpus/                      # which write-up repos have arrived
```
