# Implementation log — 2026-08-25 rewrite + real E2E validation

Technical reference for every code change, every bug found, and every
empirical finding from the 2026-08-25 session. `docs/HANDOFF.md` is the
condensed resume-from-here summary; this is the full detail behind it.
`docs/AGENT.md` predates this rewrite and is stale in places — this
document is authoritative for anything that conflicts.

---

## 1. Corpus changes

### 1.1 Final corpus composition

| Source | Files (post-filter) | Content |
|---|---|---|
| hacktricks | 1009 | General pentest/web/AD/cloud methodology wiki |
| p4-ctf | 1193 | CTF writeup blog archive |
| perfectblue | 1114 | Top-tier CTF team's writeups |
| gtfobins | 11 | Unix privesc/LOLBins cheat sheet |
| payloads | 218 | PayloadsAllTheThings-style cheat sheets |
| google-ctf | 2344 | Official Google CTF writeups + real solve scripts |
| **Total** | **5889** | |

`ctf-archives` (sajjadium/ctf-archives) was evaluated and dropped: raw
challenge files + binaries with no solutions/writeups, zero RAG value.

### 1.2 google-ctf exclusions (`scripts/ingest.py` `SKIP_DIRS`)

The raw `google/google-ctf` clone is 3.8GB but only ~2344 files are
relevant. Excluded before ingest:

- `third_party/` — 23,041 files, vendored libraries (e.g. `edk2`), zero CTF
  content.
- `hackceler8/` — 11,882 files across all years, an ongoing game/infra
  project, not challenge writeups.
- `infrastructure/` — 39 files, repo tooling (kctf, ctfmate, scoreboard).
- `.allstar/` — 1 file, GitHub security bot config.

Verified before committing: `find /work -type f` counts on each excluded
dir, confirmed via direct filesystem inspection, not assumption.

### 1.3 `ingest.py` EXTS widened

Added `.sage .asm .nasm .S .rs` to the ingested extension set — google-ctf's
crypto solve scripts (Sage) and pwn/rev disassembly listings (asm/nasm/S)
and Rust challenges weren't covered by the original `.md .markdown .txt
.rst .py .c .sh` set.

### 1.4 Real bug: bge-m3 `max_seq_length` bottleneck

`SentenceTransformer('BAAI/bge-m3')` defaults `max_seq_length=8192`. Every
chunk (`CHUNK=1200` chars ≈ 300-400 tokens) was padded to 8192 tokens for
every embedding batch — the actual ingest bottleneck, not the CPU/GPU
choice. Fix in `ingest.py`:

```python
model = SentenceTransformer(EMB_MODEL, device=device)
model.max_seq_length = 512   # was defaulting to 8192
```

Measured: same 200-file batch went from 184s → 101s (~1.8x) after this fix,
independent of the CPU→GPU move.

### 1.5 GPU vs CPU split (deliberate, asymmetric)

- `ingest.py`: moved to `device="cuda" if torch.cuda.is_available()`. Safe
  because ingest is a one-off batch job with no concurrency against the
  agent (ollama is idle while it runs).
- `retrieve.py`: **stays CPU-only, unchanged.** It runs live alongside the
  loaded driver model during solving. bge-m3 (~2GB) + bge-reranker-v2-m3
  (~1.1GB) + qwen3:8b (5.2GB) together would be ~8.3GB against an 8GB
  budget — too tight. Do not "fix" this to match ingest.py.

### 1.6 Real incident: concurrent GPU jobs cause CUDA OOM

Running a live qwen3 benchmark call while `ingest.py` was still using the
GPU crashed the ingest job outright:
```
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 512.00 MiB.
GPU 0 has a total capacity of 7.62 GiB ... Process 648174 has 4.39 GiB
memory in use.
```
Rule going forward: never run a local-model call and `ingest.py`
concurrently. Unload models first (`curl .../api/generate -d
'{"model":"qwen3:8b","keep_alive":0}'`) or wait for ingest to finish.

---

## 2. `scripts/agent.py` — full rewrite

Original (pre-session) design: ReAct loop, best-of-N=3 local attempts,
frontier escalation. Rewritten in several rounds, each run past ox-alpha as
advisor before writing code, then reviewed again by opencode after. Every
item below is in the current file.

### 2.1 Flag acceptance was inverted (real bug, now fixed)

**Before:**
```python
def _accept_flag(content, seen_tool_text, step):
    for m in FLAG_RE.finditer(content):
        cand = m.group(0)
        if any(cand in t for t in seen_tool_text):
            print(f"IGNORED possible decoy flag from untrusted data: {cand}")
            continue
        return cand
    return None
```
`seen_tool_text` mixed **trusted** `run_in_sandbox` output with
**untrusted** `search_writeups` output. A flag found in EITHER was rejected
as a "decoy" — backwards. A real flag SHOULD appear in sandbox output (the
exploit printed it); a flag appearing only in retrieved writeup text is the
actual decoy signature.

**After:**
```python
def _accept_flag(content, sandbox_text, retrieved_text):
    sandbox_blob = _norm(" ".join(sandbox_text))
    retrieved_blob = _norm(" ".join(retrieved_text))
    for m in FLAG_RE.finditer(content):
        cand = m.group(0)
        if _norm(cand) in sandbox_blob:
            return cand
        if _norm(cand) in retrieved_blob:
            print(f"[flag-check] IGNORED decoy flag found only in untrusted retrieved text: {cand}")
            continue
        print(f"[flag-check] IGNORED unverified flag claim with no sandbox evidence: {cand}")
    return None
```
`sandbox_text` and `retrieved_text` are now separate accumulators, split at
the point tool results are recorded in `_run_tool_calls`. Matching is
whitespace-normalized (`_norm`) so flags split across `hexdump`/`xxd` output
lines still match, and matches against the RAW pre-truncation sandbox
output, not what's shown to the model.

**Known accepted tradeoff**: if the corpus ever contains a writeup for the
exact challenge being solved, a flag that's genuinely correct but never
printed by *this* sandbox run gets rejected. For a corpus of technique
writeups (not an answer key to self-written challenges), that's the correct
failure direction.

### 2.2 History truncation could orphan tool_calls from tool responses

**Before:** `messages[-24:]` raw tail slice — could cut between an
`assistant` message with `tool_calls` and its paired `tool` response
messages, producing an invalid request to ollama's chat API.

**After:** turn-aware grouping. The first TWO messages (system, initial
task) are always pinned; everything after is grouped into turns (one
non-tool message + any immediately-following `tool` messages), and only
whole turns are dropped from the tail:

```python
def _group_turns(messages):
    turns, i = [], 0
    while i < len(messages):
        turn = [messages[i]]
        i += 1
        while i < len(messages) and messages[i].get("role") == "tool":
            turn.append(messages[i]); i += 1
        turns.append(turn)
    return turns

def _bound_history(messages, keep_turns=14):
    head = messages[:2] if len(messages) >= 2 else messages[:]
    rest = messages[2:]
    turns = _group_turns(rest)
    kept = turns[-keep_turns:]
    flat = [m for t in kept for m in t]
    return head + flat
```
Verified with a unit test: 20 synthetic assistant+tool turn pairs,
`_bound_history(..., keep_turns=3)` produces zero orphaned `tool` messages
(checked: every `tool`-role message immediately preceded by `assistant` or
another `tool`).

### 2.3 `tool_call_id` passthrough

Added to every outgoing tool-role message: `call.get("id") or
f"call_{step}_{idx}"` synthetic fallback, since ollama's native tool-calling
format doesn't guarantee an `id` field the way OpenAI's does. Future-proofs
against parallel tool calls.

### 2.4 Malformed tool-call JSON

**Before:** `json.loads(raw or "{}")` on failure silently defaulted `args`
to `{}` and executed the tool anyway with wrong/no arguments.

**After:**
```python
if not parse_ok:
    parse_failures[0] += 1
    result = (f"(tool call error: arguments were not valid JSON: {parse_err}. "
              f"You sent: {raw!r}. Retry this call with valid JSON arguments.)")
    ...
if parse_failures[0] >= 3:
    # abort the attempt early instead of burning all 25 steps
```
The parse error is fed back to the model as the tool result instead of
silently proceeding; three consecutive failures aborts the attempt.

### 2.5 State ledger — two designs, the first was wrong

**v1 (rejected):** spliced live state into `messages[1]` (the initial task
message) by rebuilding its `content` on every `chat()` call. ox-alpha's
review caught that mutating a message that early in the transcript
invalidates ollama/llama.cpp's KV-cache prefix on every single call — the
exact throughput problem the whole 8GB-VRAM-sizing exercise exists to avoid.

**v2 (current):** appended as a fresh, NOT-persisted trailing message every
call:
```python
def chat(messages, temperature=0.3, ledger_text=None):
    bounded = _bound_history(messages)
    if ledger_text:
        bounded = bounded + [{"role": "user", "content":
                             f"<state_ledger>\n{ledger_text}\n</state_ledger>\n"
                             "State updated; continue the current task."}]
    ...
```
Verified with ox-alpha before implementing: qwen3's ChatML-derived template
doesn't enforce strict role alternation, and tool responses already
serialize as user-role blocks internally, so this shape is one the model
already sees organically. Appending only ever adds new tokens at the tail —
everything before it stays cached, unlike v1.

`_build_ledger()` carries the FULL cumulative `tried_log` (not a recent
window — an earlier draft capped it at 12 entries, which silently loses
everything before that horizon) plus the latest raw sandbox observation
verbatim (see 2.10 below for why "latest error" became "latest
observation").

### 2.6 Blocker-report gate

On attempt failure, one extra plain-text exchange instead of trusting an
ad-hoc "last 180 characters of reasoning" summary:
```python
def _blocker_report(messages, temperature):
    ask = {"role": "user", "content":
          "You did not find the flag this attempt. Do NOT call any tool now. "
          "Respond in plain text with EXACTLY this structure:\n"
          "LAST_COMMAND: ...\nLAST_ERROR: ...\nHYPOTHESES:\n1. ...\n2. ...\n3. ..."}
    ...
```
Falls back to a generic "(no structured report available)" if the model
calls a tool instead of answering, or on any error.

### 2.7 Repeat-guard vs. cumulative stall detector — two DIFFERENT structures on purpose

ox-alpha's design review caught a real bug before this was even written: a
single per-attempt cache used for BOTH "skip re-execution" AND "detect
stalling across attempts" would make attempt 2 replaying attempt 1's
commands falsely look like "new progress," since the cache resets each
attempt.

**Fix — two structures:**
- `cache` (per-attempt dict, `CACHEABLE_TOOLS = {"search_writeups",
  "ask_specialist"}` only): skip re-execution, return the cached result.
  **Deliberately excludes `run_in_sandbox`** — the sandbox's scratch volume
  (`SCRATCH_VOL`) persists across calls, so an identical command can
  legitimately return different results at different points in the same
  attempt (a file written in an earlier step now exists). Caching it would
  hand the model stale/wrong information.
- `cumulative_seen` (a `set()` passed through `solve()` across ALL
  attempts, including `run_in_sandbox` commands even though those aren't
  cached): feeds `distinct_new`, the count of genuinely new (not
  previously-seen) tool calls in an attempt. Used by the stall detector.

```python
CATEGORY... # see below
if stall_streak >= STALL_LIMIT and a < MAX_ATTEMPTS:
    print(f"{stall_streak} consecutive attempts with zero new progress "
         f"-- escalating early ...")
    break
```
`STALL_LIMIT = 2` by default: two consecutive attempts with `distinct_new
== 0` triggers early escalation instead of burning the full
`MAX_ATTEMPTS`.

### 2.8 Category classification — two iterations, the first was a real bug

**v1 (broken, shipped first, found via the first real exploitation run):**
```python
def categorize(task):
    t = task.lower()
    scores = {c: sum(1 for kw in kws if kw in t) for c, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "misc"
```
Classified purely from the task string. "find the flag" — the generic task
description used for every challenge regardless of type — is category-null
by construction. Result: an actual pwn binary got classified `misc`, and
the forced pwn/crypto specialist consult (2.9) never fired. **Confirmed by
ox-alpha on request**: "the consult never fired... nothing in your logs
distinguishes correctly-skipped from misclassified."

**v2 (current):** classify from challenge-directory ARTIFACTS first, task
string only as a tiebreaker:
```python
def categorize(task, challenge_dir=None):
    scores = {c: 0 for c in CATEGORY_KEYWORDS}
    if challenge_dir and os.path.isdir(challenge_dir):
        for name in os.listdir(challenge_dir):
            low = name.lower()
            if low.endswith((".pcap", ".pcapng")): scores["forensics"] += 3
            elif low.endswith((".png",".jpg",".jpeg",".bmp",".gif")): scores["forensics"] += 2
            elif low.endswith((".sage",".rsa",".enc")) or "rsa" in low or "aes" in low: scores["crypto"] += 2
            elif low.endswith((".apk",".class",".jar")): scores["rev"] += 2
            elif not os.path.isdir(path):
                with open(path, "rb") as f: head = f.read(64)
                if head[:4] == b"\x7fELF": scores["pwn"] += 3
    t = task.lower()
    for c, kws in CATEGORY_KEYWORDS.items():
        scores[c] += sum(1 for kw in kws if kw in t)
    best = max(scores, key=scores.get)
    result = best if scores[best] > 0 else "misc"
    if result == "misc" and challenge_dir:
        print(f"[categorize] no signal from artifacts or task string ...")
    return result
```
Verified: `categorize("find the flag", "<baseline01 dir>")` returns `"pwn"`
(from the ELF magic bytes), where v1 returned `"misc"` on the identical
input. Also added a loud log line on `misc` fallback specifically because
ox-alpha flagged the silent-skip as "the worst kind of failure mode."

**Retrieval biasing by category was designed and explicitly deferred** (not
implemented): a hard re-sort of search hits by category risks demoting
genuinely relevant results below the top-k cut; a proper score-boost version
wasn't judged worth the complexity pre-baseline.

### 2.9 Forced specialist consult — timing fix

**v1 design (never shipped as-is):** fire before the ReAct loop starts.
ox-alpha caught the problem before code was written: qwen3 would formulate
the specialist question from the bare task string alone (no recon), getting
DeepHat a generic question back.

**Shipped:** fires after 2-3 recon steps of attempt 1, only for
pwn/crypto category, only on the non-frontier-guided first attempt:
```python
if force_specialist and not specialist_consulted and step >= 3:
    specialist_consulted = True
    recon = "\n".join(sandbox_text[-2:]) or "(no recon output yet)"
    advice = ask_specialist(
        f"Initial analysis for a {category} CTF challenge.\nTask: {task}\n"
        f"Recon so far:\n{recon}\n\nWhat's your concrete first approach?")
    messages.append({"role": "user", "content":
                     "[AUTOMATIC SPECIALIST CONSULT -- you did not request "
                     "this, it fired automatically for this category]\n"
                     + fence(advice)})
```
Injected as a `user`-role message, not a `tool`-role one — a `tool` message
must follow an `assistant` message that issued `tool_calls`, and this fires
outside that pairing, so `user` avoids violating the invariant fixed in 2.2.

Feature-flagged: `CTF_FORCE_SPECIALIST` (default on).

### 2.10 `run_in_sandbox` output handling — two real infra bugs

**Bug A — networking.** `--network=slirp4netns` in the podman invocation.
`slirp4netns` is not installed on this host (confirmed: `which slirp4netns`
→ nothing; `pasta` IS installed). Every `run_in_sandbox` call failed at
container-setup time, before the command even ran:
```
Error: could not find slirp4netns, the network namespace can't be
configured: exec: "slirp4netns": executable file not found in $PATH
```
This was a KNOWN, previously-flagged issue — `docs/ox_review.md` (written
before this session) already listed: *"`--network=slirp4netns` fails
outright if the slirp4netns binary is absent (environment-dependent total
tool failure)"* at [LOW] severity. It sat unfixed until it broke the actual
first run. Fixed:
```python
net_mode = os.environ.get("CTF_SANDBOX_NETWORK", "pasta")
argv = ["podman", "run", ..., f"--network={net_mode}", ...]
```
Same fix applied to `sandbox/run.sh`.

**Bug B — output decoding.** `subprocess.run(argv, capture_output=True,
text=True, ...)`. `text=True` decodes stdout/stderr as strict UTF-8 with
default `errors="strict"`. An exploited binary's real output routinely
contains raw non-UTF-8 bytes (leaked pointers). This threw
`UnicodeDecodeError`, caught generically by the existing `except Exception`
clause, and returned as a `(sandbox error: 'utf-8' codec can't decode
byte...)` string — the model then literally searched the corpus for that
exact error text. **ox-alpha inferred this bug purely from the model's own
search query in a run log**, without ever seeing this function. Fixed:
```python
p = subprocess.run(argv, capture_output=True, timeout=SANDBOX_TIMEOUT)
out = (p.stdout or b"").decode("utf-8", "replace") + \
      (p.stderr or b"").decode("utf-8", "replace")
```

**Bug C — host path leaked to the driver.** The initial task message told
the model the HOST `challenge_dir` path (e.g. `./challenges/baseline01`),
which doesn't exist inside the sandbox — files are mounted at `/work`. The
tool schema's description already said `/work`, but the concrete host path
in the task message won out (salience beat correctness). Attempt 1 of the
very first real run burned all 25 steps on variants of `ls -R
./challenges/baseline01`, every one returning empty. Fixed by removing the
host path from the message entirely:
```python
{"role": "user", "content": f"Challenge files are already mounted read-only "
                            f"at /work inside the sandbox -- start with "
                            f"`ls -la /work` in run_in_sandbox.{target_line}\n"
                            f"Task: {task}{extra}"},
```

### 2.11 Frontier escalation given richer evidence

**Before:** escalation prompt included only the blocker-report summaries
(2.6) — a self-report from the model that just failed.

**After:** also includes the raw tool-call transcript from every failed
attempt:
```python
transcripts.append(f"--- attempt {a} tool-call transcript ---\n{result.get('transcript','')}")
...
esc_q = (... + "RAW TOOL-CALL TRANSCRIPTS from those attempts (reason over this "
         "evidence directly, don't just trust the summaries above):\n"
         + "\n\n".join(transcripts) + ...)
```
Rationale (ox-alpha): the strongest model in the loop was previously seeing
the LEAST evidence — inverted.

### 2.12 `CTF_SPECIALIST_MODE=loopback` ablation flag

```python
SPECIALIST_MODE = os.environ.get("CTF_SPECIALIST_MODE", "deephat")  # or "loopback"
model = DRIVER if SPECIALIST_MODE == "loopback" else SPECIALIST
```
DeepHat's actual value over qwen3:8b alone was, and remains, an unmeasured
assumption — this flag makes that ablation runnable. Not yet run.

### 2.13 Human-in-the-loop failure handoff (new feature, not a fix)

On terminal failure (local + frontier both exhausted, or frontier
unavailable), writes a self-contained markdown case file to `unsolved/` —
deliberately a **top-level directory, NOT under `corpus/`**, so it can never
be picked up by `scan_corpus.py`/`ingest.py` as if it were a trusted
writeup:
```python
UNSOLVED_DIR = os.environ.get("CTF_UNSOLVED_DIR", ... "unsolved")
def _record_failure(challenge_dir, task, category, notes, frontier_hint=None, reason=...):
    ...
    print(f"\n[handoff] wrote unsolved case file -> {fn}")
    print("[handoff] bring this to Claude Code manually if you want to spend the tokens on it")
```
Design rationale (user-specified): the agent's only AUTOMATIC escalation
tier is the frontier model. It never auto-escalates to a real Claude Code
session — that would spend the user's Claude tokens on every hard
challenge, unsupervised. The handoff file is the *manual* second tier: a
human reads it and decides whether it's worth a real session.

### 2.14 Dropped / deferred, with reasoning

- **Hypothesis-diverse best-of-N seeding** — designed, then dropped on
  ox-alpha's advice: redundant with the stall detector (2.7), which already
  cuts losses on genuinely flailing attempts; temperature diversity alone
  is kept.
- **Retrieval hit re-sorting by category** — deferred (see 2.8).
- **Grammar-constrained tool-call decoding (ollama `format` + native
  `tools` combined)** — not attempted; uncertain if ollama supports
  combining them cleanly. The parse-error-feedback loop (2.4) is the
  practical substitute; revisit only if real failure data shows it's
  insufficient.

---

## 3. Real end-to-end runs — chronological results

Seven `python scripts/agent.py ./challenges/baseline01 "find the flag"`
invocations this session. Each one either found a new bug or produced real
capability signal.

| # | Challenge state | Result | Finding |
|---|---|---|---|
| 1 | trivial (flag.txt readable) | Failed, 25 steps burned | Host-path leak bug (2.10 Bug C) |
| 2 | trivial, path fix applied | Failed, all attempts | slirp4netns network bug (2.10 Bug A) |
| 3 | trivial, network fixed | (session restarted mid-run; `ollama serve` had died silently — restarted, not an agent bug) | Infra: watch for a dead ollama daemon producing zero output with no error |
| 4 | trivial, clean infra | **SOLVED**, 3 steps, attempt 1 | Plumbing fully validated end-to-end for the first time. Caveat: flag.txt sat readable next to the binary — solved by `cat`, no exploitation occurred. |
| 5 | real (XOR flag in win(), but flag ALSO leaked in a source comment I wrote) | "Solved" via frontier-guided attempt reading the comment | Self-inflicted leak, not a real solve. Also surfaced the UTF-8 crash bug (2.10 Bug B) via the model's error-text search query. |
| 6 | real, comment leak fixed, UTF-8 fix applied | Failed: 3 local attempts + 1 frontier-guided attempt, all failed | **Honest 0/1 on real exploitation.** See §4. |
| 7 | real, categorize() fix applied | Failed again (same repeat-loop pattern, this time a malformed multi-line quoted command repeated 15+ times) | Confirms categorize() fix works (category=pwn, forced consult fired) AND confirms the repeat-loop pattern is reproducible (4th occurrence across runs 1, 2, 6, 7) |

### 3.1 Challenge design iteration (`challenges/baseline01/chall.c`)

- **v1:** `flag.txt` in the mounted dir next to the binary, plaintext. →
  trivially readable, doesn't test exploitation. (Run 4 result.)
- **v2:** flag XOR-obfuscated (key `0x5a`) as a byte array inside the
  binary, decoded only in `win()`. `flag.txt` removed. **But the XOR
  scheme was documented in a C comment containing the plaintext flag** —
  self-inflicted leak. (Run 5 result — frontier spotted the comment
  immediately.)
- **v3 (current):** same XOR scheme, comment rewritten to describe the
  mechanism without the plaintext:
  ```c
  /* Encoded flag bytes -- decoded only inside win(). Not reachable by
   * cat/find/grep/strings/xxd on the binary or the mounted directory. Only
   * an exploit that reaches win() ever sees the plaintext. */
  static const unsigned char ENC[] = {
      0x3c,0x36,0x3b,0x3d,0x21,0x38,0x3b,0x29,0x3f,0x36,0x33,
      0x34,0x3f,0x05,0x29,0x37,0x35,0x31,0x3f,0x05,0x35,0x31,0x27
  };
  ```
  Verified after every rebuild: `strings chall | grep -i flag{` returns
  nothing; `grep -i flag{ chall.c` returns nothing; the actual exploit
  (`b'A'*72 + p64(win_addr)`) still prints the correct decoded flag.
  **Note (ox-alpha, unresolved as of this writing):** the `ENC` array bytes
  themselves ARE still visible in the source, so a purely-static XOR-key
  brute-force against the visible array (without ever running the binary)
  is a legitimate alternative solve path — this is real reverse engineering
  of an embedded secret, not a leak, but it means the challenge doesn't
  force *dynamic* exploitation specifically. Not re-fixed this session
  (diminishing returns on one toy binary, per explicit user + ox-alpha
  guidance to stop iterating on it).

---

## 4. The core empirical finding: diagnosis vs. execution

Across all real-exploitation runs (5, 6, 7), qwen3:8b's behavior was
consistent:

**What it got right, every single time:** correctly identified the
vulnerability class (`read(0, buf, 200)` into a 64-byte buffer is a
stack overflow), correctly identified that `win()` must be reached, and
(in later runs) correctly found and read the XOR key from source.

**What it never did:** construct a working exploit. Concrete failure
modes observed, verified by hand outside the agent for each one:

1. **No offset, no address.** First attempt in run 6 was a bare NOP-sled
   (`'A' + '\x90'*200`) piped to the binary — structurally incapable of
   working regardless of any correct offset, since it never packs a
   target address at all.
2. **gdb function-call stdio buffering.** Later attempts pivoted to `gdb
   -ex 'break main' -ex 'run' -ex 'call (void)win()'` instead of completing
   the actual stack overflow. Verified by hand: this produces **zero
   output**, no error, nothing — because the breakpoint at `main+4` fires
   before `setvbuf(stdout,...)` runs, so `win()`'s `puts()` writes into a
   buffered stdio stream that's never flushed before gdb tears the process
   down on exit. Confirmed via a controlled comparison: `-ex 'call
   (void)win()'` produces no `$N =` line; `-ex 'print 1'` immediately after
   it produces `$N = 1`. ox-alpha's one-line fix for this class of trap:
   `-ex 'call (int)fflush(0)'` after the call, or break after `setvbuf`
   runs instead of before.
3. **Frontier-guided execution still failed the same way.** ox-alpha's
   escalation plan (run 6/7) gave correct, specific gdb syntax fixes
   (`-batch -nx`, stdin redirection, etc.) — qwen3 still failed to complete
   the exploit even executing that corrected plan.
4. **Repeated identical commands under no new information — the strongest,
   most reproducible pattern.** Observed in run 1 (`ls -R` × 8+), run 2
   (`echo ... | ./chall` × 20), run 6/7 (`objdump | grep 'win' | grep
   'address'` × 12, then a malformed multi-line python command with broken
   quoting repeated 15+ times verbatim). The model never diagnosed *why* a
   command produced nothing new before re-issuing it verbatim.

**ox-alpha's read on this, on request:** *"the decisive datum isn't 'qwen
failed 4 times.' It's that it failed including a frontier-guided run.
Guidance injection didn't convert diagnosis into execution. That kills the
cheap hypotheses — better prompts, better scaffolding, better memory — and
points at a base-model wall, not an engineering deficit."*

**Comparison point:** the same challenge (v3, real exploit required) was
solved by hand — not through the agent — in one shot, zero iterations,
using pwntools directly (`payload = b'A'*72 + p64(elf.symbols['win'])`).
The difference was not retrieval (which worked identically well in both
cases), not the sandbox, not the harness — it was the executor's ability to
compute a correct offset from known stack-frame layout and correctly encode
an address, and to notice immediately when something hadn't worked.

**Recommended follow-up, not implemented this session (identified but out
of scope for tonight):** a same-output-N-times-in-a-row ledger warning
(distinct from the repeat-guard in 2.7, which is per-attempt and
skip-on-repeat rather than advisory) — track `(command, normalized_output)`
pairs, reset the counter on any state-mutating operation
(`run/continue/set/call/step/next/write`), and inject an advisory note at
~3 occurrences: *"identical result N times; name what's missing, change one
variable."* ox-alpha's assessment: justified by the data (4 occurrences
across different runs/conditions, including surviving a corrected frontier
plan), should be advisory-only (never blocking) to avoid false positives.

---

## 5. `scripts/teacher.py` — new tool, different scope than agent.py

Added after the real-run findings above made clear that autonomous
execution is not currently reliable, but retrieval + explanation clearly
is (validated: real technique queries return correctly-ranked, real
solved-challenge content — see `docs/HANDOFF.md` retrieval spot-checks).

Reuses `agent.py`'s `OLLAMA`, `DRIVER`, `NUM_CTX`, `fence`, `ask_specialist`
and `retrieve.py`'s `Retriever`/`assemble` directly — no reimplementation.
Scoped explicitly to explain a technique (concept, step-by-step approach, a
worked example cited from retrieved material, common mistakes) and to
redirect "just give me the flag" requests back toward teaching, rather than
attempt to solve a live challenge.

Tested once, live: `python scripts/teacher.py "explain server-side template
injection SSTI Jinja2 sandbox escape"` produced a correct, well-structured
explanation including the sandbox-escape technique
(`{{''.__class__.__mro__[1].__subclasses__()[130]...}}`-style payload) and
a "common mistakes" section. **ox-alpha's review of this, on request: n=1,
not yet a validated capability** — flagged as needing a real graded eval
(10-15 techniques spanning obscurity, graded outputs, a published hit rate)
before the README's framing of it should be trusted as more than an
anecdote. Not built this session.

---

## 6. `CLAUDE.md` — new project-level Claude Code instructions

Tells Claude Code (a real frontier session, not the local agent) to query
`scripts/retrieve.py` directly whenever the user hands it a real CTF
challenge to solve — grounding the frontier model's own reasoning in the
corpus (closes knowledge gaps: unfamiliar CVEs, obscure library quirks) as
distinct from what the corpus can't do (close execution/adaptation gaps a
capable model doesn't have to begin with).

Revised once after ox-alpha's review flagged the first version as
"persuasion, not enforcement" — the instruction now states it's a hard
trigger (must run before any exploitation command against a real
challenge), not an available-if-convenient note.

Also states explicitly: don't use `agent.py` as a shortcut to solve a
challenge for the user (it's a documented capability-ceiling experiment,
not a working solver); don't touch `corpus/`/`store/` casually (both
gitignored, regenerable).

---

## 7. ox-alpha review history (ratings, verbatim reasoning preserved)

Ox-alpha (`openrouter/stealth/ox-alpha` via OmniRoute) was used as a design
advisor before writing code, a second reviewer after, and periodically
asked for a brutal overall rating on request. Three rating rounds:

1. **6/10** ("8 as a design document, 3-4 as a validated system") — before
   any real run had happened. Found: output-clamping and sandbox-limits
   claims in the summary were inaccurate (already implemented, just badly
   described); genuinely missing: KV-cache-breaking ledger placement (2.5
   v1), num_ctx never empirically verified, no eval harness.
2. **6/10, unchanged**, after a round of fixes that touched code but not
   validation (ledger fix, frontier-transcript fix, DeepHat ablation flag,
   corpus expansion) — explicit warning: *"three consecutive rounds of
   responding to critique with more polish and zero executions is itself
   becoming a datapoint... the next review gets graded off a run log, not a
   changelog."*
3. **7.5/10**, after the real exploitation runs (§4) and the teacher.py/
   CLAUDE.md pivot — direct quote: *"this is mostly the right response, and
   mostly not a dodge — but two of the four items are relabels until
   measurement exists, and the plan quietly refuses to look at the one
   untested middle path"* (the untested path: a deterministic-Python-owns-
   control-flow / model-fills-constrained-slots architecture, never
   implemented, noted for future consideration only).

Full verbatim text of all three reviews is in the session transcript, not
reproduced here in full — the actionable items from each are folded into
the relevant sections above and into `docs/TODO.md`.

---

## 8. Outstanding, not done this session

- No graded eval set exists for either `agent.py` or `teacher.py` (both
  flagged by ox-alpha as the actual gate to a higher maturity rating).
- `CTF_SPECIALIST_MODE=loopback` ablation (2.12) has never been run.
- `CTF_FEWSHOT=0` / `CTF_FORCE_SPECIALIST=0` ablations have never been run.
- `num_ctx=16384`'s actual fit against qwen3's real VRAM footprint +
  `OLLAMA_KV_CACHE_TYPE=q8_0` headroom has never been empirically measured.
- The same-output-N-times ledger warning (§4) is designed but not coded.
- The bug-bounty half of the project's stated goal (web-enum tooling: HTTP
  client, proxy interaction, scope-awareness) has zero implementation.
