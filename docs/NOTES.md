# ctf-agent — build notes & reference

Running log of decisions, measurements, and gotchas so they don't have to be
re-derived. Newest sections are appended over time.

---

## 1. Hardware (the box this runs on)

| Part | Spec | Consequence |
|---|---|---|
| GPU | RTX 4060 **Laptop** (AD107M), **8 GB** VRAM, cc 8.9 | Hard ceiling. Not the 16 GB desktop card. |
| iGPU | Intel UHD (Raptor Lake) | Drives the display, so nearly all 8 GB GPU VRAM is usable for models. |
| CPU | i7-14700HX, 20C / 28T | Embeddings + rerank run here to spare VRAM. |
| RAM | 16 GB (~6 GB free, already swapping) | Caps CPU-offload / MoE. Close Brave for 20–30B runs. |
| RAM read BW | **~24.8 GB/s** (measured, single-channel) | Sets CPU-side tok/s. Adding a 2nd SODIMM ~doubles it. |
| Disk | 953 GB NVMe | Was 86% full; cleaned to ~39%. |

### Measured LLM performance (7B Q4_K_M, 100% GPU, via ollama)

| Context | KV cache | VRAM | gen tok/s | prefill tok/s |
|---|---|---|---|---|
| 4k | f16 | 4.85 GB | 53.3 | — |
| 16k | f16 | 6.4 GB | 43.9 | 717 |
| 32k | q8_0 + FlashAttn | 6.8 GB | 21.8 | 1042 |

**Free 2× context win:** `OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`
doubled usable context (16k→32k) for +0.2 GB VRAM, and prefill got *faster*.

**Prefill is the RAG bottleneck**, not corpus size: ~1042 tok/s means 8k of
retrieved context ≈ 8 s before first token. → the reranker (retrieve wide,
feed few) is a *latency* fix as much as a quality one.

---

## 2. Model choices

**Picks (both fit the 8 GB path via MoE / offload):**
- `gpt-oss:20b` — primary. MoE (~3.6B active/token), built for agentic tool loops
  = exactly the pwn loop. Best speed/capability balance here.
- `qwen3:30b-a3b` — heavier alt. Slightly stronger reasoning, a bit slower.
  Needs Brave closed for RAM headroom. Prefer llama.cpp `--n-cpu-moe` over
  ollama for expert offload.

**Why MoE:** only path past ~8B that stays usable — active params per token is
~3–3.6B, not 20–30B. **Dense 32B is dead** on this box (~1.3 tok/s, RAM-BW bound).

**Other local options worth knowing (2026):**
- Qwen3 8B / 14B — the practical daily-driver family for local.
- DeepSeek-R1 distills — reasoning-heavy debugging.
- DeepHat (formerly WhiteRabbitNeo) — security-specialized / "uncensored" tuned.
- Phi-4 — if hardware is weaker.

Sources: HuggingFace open-LLM roundups 2026; StationX "Best Local LLM Aug 2026";
TrustedSec "Benchmarking Self-Hosted LLMs for Offensive Security"; Bishop Fox LLM
CTF lab. Benchmark takeaway: for offensive tool-use, **tool/prompt description
quality matters more than model size** — validates this project's tool-loop focus.

---

## 3. Local vs frontier for CTF (honest)

**Local 20–30B is NOT a better brain than Claude Code / GPT / Gemini for CTF.**
The gap is largest exactly where CTF is hardest: novel exploit reasoning, chain
construction, unusual logic. Frontier wins raw solving power, code-gen for
exploits, and reliable multi-step tool use.

**Where this local rig wins:** offline / air-gapped comps, privacy (challenge
never leaves the box), zero cost + no rate limits (grind thousands of iterations),
and RAG over *your own curated* writeup corpus.

**Practical split:** local for recon, known-technique lookup, grinding, offline
rounds; frontier for the hard novel logic. This project is the local half.

---

## 4. Architecture & security (summary)

Pipeline: writeups → `scan_corpus.py` (security gate) → `ingest.py` (bge-m3 embed
on CPU → chromadb) → `retrieve.py` (dense + bge-reranker on CPU, chunks fenced as
untrusted data) → agent loop (local LLM + tools) → podman sandbox (tool exec).

Threat model: corpus is **untrusted** and feeds an agent that runs commands.
Defense = (1) fence every retrieved chunk as data at prompt assembly [primary],
(2) `scan_corpus.py` flags/quarantines only genuine agent-directed poisoning —
NOT technique keywords (writeups legitimately contain "ignore instructions",
exfil, role tokens), (3) rootless podman, cap-drop ALL, read-only, resource-
capped, egress to target only. Scan on first 231 files: 202 allow / 29 flagged /
**0 false quarantine**.

---

## 5. Gotchas / incidents (so they don't bite again)

- **RTX 4060 "16 GB" is system RAM, not VRAM.** VRAM is 8 GB. This drives every
  model-size decision.
- **`hf download REPO --exclude "*.onnx" "onnx/*"`** → the CLI treats the 2nd
  pattern as a positional filename and prints *"Ignoring --exclude since filenames
  have been explicitly set"*, silently downloading only tokenizer files. Fix: no
  positional filenames — plain `hf download REPO` (disk is cheap here).
- **hf-xet backend can hang at 0 bytes** on a flaky/slow link while a sibling
  download progresses. If a blob sits at 0 for minutes, restart that download.
- **git clone over slow links drops with HTTP/2 CANCEL** (early EOF). Fix:
  `git config --global http.version HTTP/1.1` + `http.postBuffer 524288000` +
  a retry loop. HackTricks/p4/perfectblue needed this.
- **ollama pull does NOT survive a wifi switch** — the in-flight blob restarts
  from 0 (chunk resume lost on network change). Finish big pulls on stable wifi.
- **Bandwidth contention:** parallel HF + git + ollama pulls on one slow link
  starve each other. Serialize by priority.
- **Kill by explicit PID**, never `for p in $(pgrep -f PATTERN)` — the pattern can
  match the running shell and signal it (seen as exit 144).

---

## 6. Command reference

```bash
# serve ollama with the free 2× context win
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve

# corpus
scripts/fetch_corpus.sh                 # clone public writeup repos
python scripts/scan_corpus.py           # security scan -> store/scan_manifest.json
python scripts/ingest.py --reset        # embed + index (CPU)
python scripts/retrieve.py "ret2libc no leak"   # query test

# sandbox
podman build -t ctf-sandbox:1 sandbox/
sandbox/run.sh <challenge_dir> <host> <port>

# check VRAM fit at your context: if >~7.5 GB, cut num_ctx or quant
nvidia-smi --query-gpu=memory.used --format=csv
```

---

## 7. Status (living)

Done: hardware benchmarked; disk cleaned (~43 GB freed); security scanner built +
validated; ingest / retrieve / sandbox code written; corpus partially cloned
(gtfobins, payloads, ctf-archives; hacktricks/p4/perfectblue re-fetching); repo
pushed private.

Downloading: gpt-oss:20b, then qwen3:30b-a3b; bge-m3 + bge-reranker-v2-m3 (paused
during gpt-oss, resume after).

Next: finish downloads → scan+ingest full corpus → build the agent loop (ReAct:
local LLM + {search_writeups, run_in_sandbox}) → end-to-end test incl. a planted-
injection check to prove the fencing holds.
