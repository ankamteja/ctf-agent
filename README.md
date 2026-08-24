# ctf-agent

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
              │   ingest     │  chunk → bge-m3 embed (CPU) → chromadb
              └──────┬───────┘
                     ▼
              ┌──────────────┐   dense + rerank (bge-reranker, CPU)
   query ────▶│  retrieve    │   returns chunks fenced as UNTRUSTED data
              └──────┬───────┘
                     ▼
              ┌──────────────┐   local LLM (gpt-oss:20b / qwen3:30b-a3b via ollama)
              │  agent loop  │   tools: search_writeups, run_in_sandbox
              └──────┬───────┘
                     ▼
              ┌──────────────┐   rootless podman: cap-drop ALL, read-only,
              │  sandbox     │   mem/cpu/pid caps, egress to target only
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
   container, all capabilities dropped, read-only root, resource-capped, network
   restricted to the challenge target.

## Hardware target

RTX 4060 Laptop (8 GB VRAM), i7-14700HX, 16 GB RAM. Measured on this box:
7B/MoE Q4 with `OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0` holds 32k
context in ~6.8 GB VRAM. Embeddings and reranking run on CPU to leave the whole
GPU for generation. Dense >13B models are impractical (RAM bandwidth bound).

## Layout

```
scripts/
  scan_corpus.py   corpus security scan → store/scan_manifest.json
  ingest.py        corpus → chromadb (bge-m3 on CPU)
  retrieve.py      hybrid retrieve + rerank, injection-safe assembly
sandbox/
  Containerfile    locked-down pwn image (pwntools, gdb, ROPgadget, ...)
  run.sh           launch one challenge sandbox
corpus/            (gitignored) cloned public writeup repos
store/             (gitignored) chromadb + scan manifest
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
```

## Status

Work in progress. Corpus ingest, retrieval, sandbox, and security scan are in
place; the agent loop and end-to-end test are being wired as the local models
finish downloading.

## Corpus sources

Public reference/writeup repositories (each retains its own license): GTFOBins,
PayloadsAllTheThings, HackTricks, p4-team/ctf, perfectblue/ctf-writeups,
sajjadium/ctf-archives. The corpus itself is not redistributed here.
