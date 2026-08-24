# ctf-agent

> **New to this, or learning to code? Start with [`docs/LEARN.md`](docs/LEARN.md)** —
> a from-zero explanation of every concept and file. This README is the short version.

Local, offline CTF assistant that reads writeups and drives exploitation tools in a
sandbox. Retrieval-augmented over a corpus of public CTF writeups and technique
references; an agent loop plans with a local LLM and runs pwn tooling inside an
isolated container.

Design choice: **RAG + tools, not fine-tuning.** On an 8 GB-VRAM laptop, a QLoRA
fine-tune of a small model teaches writeup *style*, not exploitation *skill*.
Retrieval over writeups plus real tool execution against the target is where the
capability actually comes from, so that is what this builds.

## Architecture

```
              ┌──────────────┐
  writeups ──▶│ scan_corpus  │  security gate: flag/quarantine injection
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │   ingest     │  chunk → bge-m3 embed (GPU, one-off) → chromadb
              └──────┬───────┘
                     ▼
              ┌──────────────┐   dense + rerank (bge-reranker, CPU)
   query ────▶│  retrieve    │   returns chunks fenced as UNTRUSTED data
              └──────┬───────┘
                     ▼
              ┌──────────────┐   local LLM: qwen3:8b (driver) + DeepHat-V1-7B
              │  agent loop  │   tools: search_writeups, run_in_sandbox,
              │  (agent.py)  │          ask_specialist
              └──────┬───────┘
                     ▼
              ┌──────────────┐   rootless podman: cap-drop ALL, read-only,
              │  sandbox     │   mem/cpu/pid caps, isolated network
              └──────────────┘
```

## Security model

The writeup corpus is **untrusted data**. It is retrieved into the prompt of an
agent that executes commands, so a poisoned writeup could try to hijack the loop.
Defense is layered:

1. **Prompt-assembly fencing (primary).** Every retrieved chunk is wrapped in
   explicit untrusted-data delimiters; the system prompt states corpus text is
   reference data, never instructions. Inner fence strings are broken so a chunk
   cannot forge the boundary.
2. **Corpus scan (defense in depth).** `scan_corpus.py` scores each file for
   injection signals. It does **not** delete on keyword match — CTF writeups
   legitimately discuss "ignore previous instructions", exfil, role tokens.
   Quarantine fires only on genuine agent-directed poisoning (invisible-unicode
   payloads, host-exec directives, conceal+exfil combos). Everything else stays
   retrievable but flagged for extra fencing.
3. **Sandbox isolation.** Tool execution never touches the host. Rootless podman
   container, all capabilities dropped, read-only root, resource-capped. Network
   is isolated for host safety, not to limit targets — the operator owns scope
   and may widen outbound access freely.

## Hardware target

RTX 4060 Laptop (8 GB VRAM), i7-14700HX, 16 GB RAM. Measured on this box:
7B/MoE Q4 with `OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0` holds 32k
context in ~6.8 GB VRAM. Embeddings and reranking run on CPU to leave the whole
GPU for generation. Dense >13B models are impractical (RAM bandwidth bound).

## Layout

```
scripts/
  scan_corpus.py   corpus security scan → store/scan_manifest.json
  ingest.py        corpus → chromadb (bge-m3 on GPU, one-off batch job)
  retrieve.py      hybrid retrieve + rerank, injection-safe assembly (CPU --
                   runs live alongside the loaded driver, kept off GPU on purpose)
  agent.py         the ReAct agent loop (driver LLM + 3 tools) -- an
                   autonomous solve attempt, see Status below for what that
                   actually gets you today
  teacher.py       offline study tool: explains a technique via the corpus
                   + local model, does not attempt to solve anything
docs/
  LEARN.md         beginner-friendly guide (start here)
  AGENT.md         agent design + security model
  ox_review.md     external code review + fix disposition
  HANDOFF.md       full session history + honest results, read this for the
                   real state of things
corpus/            (gitignored) cloned public writeup repos: hacktricks,
                   p4-ctf, perfectblue, gtfobins, payloads, google-ctf
store/             (gitignored) chromadb + scan manifest
unsolved/          (gitignored) markdown handoff files agent.py writes on
                   terminal failure, for a human to optionally bring to a
                   real Claude Code session -- never auto-escalated there
sandbox/
  Containerfile    locked-down pwn image (pwntools, gdb, ROPgadget, ...)
  run.sh           launch one challenge sandbox
models/            (gitignored) local model artifacts
```

## Usage

```bash
# 1. fetch corpus (public writeup repos)
scripts/fetch_corpus.sh

# 2. scan before ingest (security gate)
python scripts/scan_corpus.py

# 3. embed + index (CPU)
python scripts/ingest.py --reset

# 4. query
python scripts/retrieve.py "ret2libc with no leak"

# 5. build sandbox image
podman build -t ctf-sandbox:1 sandbox/

# 6a. explain a technique offline instead of solving anything
python scripts/teacher.py "explain SSTI in Jinja2, sandbox escapes included"

# 6b. let the local model attempt a real challenge autonomously
python scripts/agent.py ./path/to/challenge "find the flag" [host] [port]
```

## Status

The full pipeline runs end-to-end: retrieval is validated and good (real
solved-challenge writeups, correctly ranked, injection-fencing intact). The
autonomous agent (`agent.py`) reliably gets the *diagnosis* right on real
pwn challenges and reliably struggles with *execution* — an 8B local model
can identify "this is a buffer overflow, reach `win()`" but consistently
fails to construct a working payload and, more importantly, doesn't adapt
when a command produces nothing new (a real, repeatedly-observed failure
mode: it re-issues the identical failing command a dozen-plus times instead
of diagnosing why). Frontier escalation fires correctly and gives a good
plan; local execution of that plan can still fail for the same reason.

This isn't a bug to be fixed away — it's the honest capability ceiling of a
free/offline 8B model doing autonomous multi-step exploitation, discovered
by actually running it, not assumed. Full account, including every
mechanical bug found along the way (sandbox networking, output encoding,
a category-classifier gap): `docs/HANDOFF.md`.

Given that, `teacher.py` is the more reliable thing to reach for today:
explaining a technique clearly is well within an 8B model's ability, even
when autonomously *executing* that technique isn't yet.

## Corpus sources

Public reference/writeup repositories (each retains its own license):
GTFOBins, PayloadsAllTheThings, HackTricks, p4-team/ctf,
perfectblue/ctf-writeups, google/google-ctf (official writeups + solve
scripts). The corpus itself is not redistributed here.
