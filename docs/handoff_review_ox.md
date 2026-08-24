**(1) Missing info that blocks resuming**
- Exact ollama tags unverified: spec hardcodes `"qwen3:8b"` / `"DeepHat/DeepHat-V1-7B"`; DeepHat may need `hf.co/...` pull or a custom Modelfile name — if the pulled tag differs, `ask_specialist` silently breaks. Confirm against `ollama list`.
- `$OMNIROUTE_API_KEY`: where it's stored/exported; no fallback if OmniRoute is down.
- Sandbox image status: Containerfile exists, but whether `ctf-sandbox:1` was actually built is unstated.
- Was `ingest.py` ever run (is `store/` chromadb populated), or script-only? "BUILT" conflates written vs. executed throughout.
- Which corpus snapshot `scan_corpus.py` validated (202/29)? If `finish_downloads.sh` adds more, does scan need a rerun before ingest? Unstated.
- Canonical task list behind IDs #3/#4/#7/#8/#9/#10/#11 isn't in the handoff — IDs are unresolvable cold.
- Python env for scripts (venv? miniforge? pinned deps) undocumented.
- E2E planted-injection recipe undefined: what to plant, where, how to revert.
- No example `challenge_dir` layout — the "known challenge" for E2E is unspecified.
- `~/Downloads/ask_ox.py` lives outside the repo; no backup path given.
- Repo branch/commit not pinned for cold resume.

**(2) Technical/factual errors**
- "Both fit 8 GB fully at ~40+ tok/s" is false jointly: 5.2 + 4.7 GB > 8 GB. Driver + specialist can't co-reside; ollama will load/unload-thrash on every `ask_specialist` call.
- Direct self-contradiction: spec SECURITY says the denylist is "critical, must implement"; USER DIRECTIVE says ignore it. A cold reader hits both. Worse, the ignore order targets only half a sentence (drop network-scope, keep host-escape checks) with no guidance on splitting it.
- Three conflicting network postures: `run.sh` "egress to target only" vs. spec's `--network=slirp4netns` (full outbound) vs. directive "full outbound, widen freely."
- "stdlib + `requests` only" contradicts the mandatory `from retrieve import ...` (pulls in chromadb + embedding stack).
- "Env vars for ollama options: … num_ctx=16384, temperature=0.3" — `num_ctx`/`temperature` are chat-API `options`, not env vars; only `OLLAMA_FLASH_ATTENTION` is.
- "0 false-quarantine" asserted with no review method for the 29 flagged docs.

**(3) Unclear step ordering**
- Step 4 (ingest + retrieval test) is listed *after* step 1 (agent build), but the agent's `search_writeups` depends on a working index — retrieval verification should precede agent construction.
- Corollary unstated: if corpus lands after the scan manifest was made, the rescan → re-ingest sequence is undefined.
- Task IDs are non-monotonic (7→11→9→3/4→8); labeled "in order" but reads like typos. Should say explicitly it's priority order, not ID order.
- Optimization (step 2) is scheduled before any E2E baseline (step 5) — tuning before correctness is demonstrated.
- Models/corpus still downloading: step 1 coding can start, but nothing is testable until pulls finish — dependency implied, never stated.
- CLI accepts `[host] [port]` but no tool signature consumes them; intended routing unstated.
- Fencing is layered twice: `assemble()` already fences chunks, then the spec wraps tool messages again and "neutralizes inner fence-strings" — which layer is authoritative is ambiguous (risk of mangled escapes).

---
model: stealth/ox-alpha | usage: {'prompt_tokens': 2777, 'completion_tokens': 7897, 'total_tokens': 10674, 'prompt_tokens_details': {'cached_tokens': 64, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}
