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

## In progress ⏳

- [ ] **Corpus download** — cloning the write-up libraries (hacktricks, p4-ctf,
      perfectblue, ctf-archives). Runs in the background via
      `scripts/finish_downloads.sh` (safe to re-run; it skips what's already
      there). Check with: `tail logs/finish.log` and `ls corpus/`.

## Next, in order (revised — highest impact first) 📋

Rationale: the frontier model does the hard reasoning, so the biggest wins are
the things that decide **how often we avoid paying for it** (routing + good
retrieval), plus the loop that makes the system **compound** (memory). Best-of-N
and the DeepHat specialist are lower-leverage — prove them against a real
baseline before investing more.

0. [ ] **First end-to-end run = get a baseline** (needs corpus ingested).
   Solve ONE easy challenge, measure the real *local* solve rate. Everything
   below is prioritized by what that number turns out to be. Don't tune blind.
   ```bash
   python scripts/scan_corpus.py && python scripts/ingest.py --reset
   python scripts/agent.py ./path/to/easy_challenge "find the flag"
   ```
1. [ ] **Difficulty/category routing.** Classify the challenge first. Easy /
   known-pattern -> local attempts. Novel pwn/crypto -> escalate immediately,
   skip the wasted local tries. Stops burning slow attempts on the hopeless.
2. [ ] **Exemplar-with-code retrieval (RAG quality).** The real capability
   driver. Keep exploit code blocks intact when chunking; retrieve SOLVED
   write-ups *with their working scripts*, not just prose; inject the nearest
   solved exemplar as a worked example (few-shot). Consider a code-aware embedder.
3. [x] **Solve-memory (compounding).** DONE — every solve is written back to
   `corpus/solved/` as a trusted exemplar (see `agent.py:_record_solution`), so
   the system gets better the more it's used. Re-ingest to make new solves
   searchable.
4. [ ] **Constrained tool-calls.** Force the model's tool requests into a strict
   JSON shape (ollama `format`) so it stops malforming them — the #1 local
   failure mode.
5. [ ] **Category playbooks + tools.** pwn/crypto/web/rev/forensics need
   different tools + prompts; a generic pipeline underperforms. Add per-category
   tool sets and recon tools (subfinder/httpx/nuclei/ffuf; use `~/go/bin/httpx`).
6. [ ] **Injection safety test.** Plant a fake instruction in a write-up; confirm
   the agent treats it as data and refuses to obey.
7. [ ] **Re-evaluate DeepHat.** Once there's a baseline, check whether the local
   specialist actually earns its 4.7 GB + swap cost vs. driver + frontier alone.

## Optional / decisions 🔧

- **DeepHat specialist model — downloading (fixed).** DeepHat-V1-7B is a strong
  uncensored, security-tuned 7B — a good LOCAL specialist for CTF/exploit work.
  The bare ollama tag `DeepHat/DeepHat-V1-7B` pulls a 15 GB full-precision (F16)
  copy that will NOT fit the 8 GB GPU, so we switched to a quantized GGUF that
  fits: `hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q4_K_M` (~4.7 GB). It's pulling in
  the background now. Note it still can't sit in VRAM at the same time as qwen3
  (5.2 + 4.7 > 8 GB), so ollama swaps them when `ask_specialist` is called — fine,
  each runs fast alone. If DeepHat is absent, `ask_specialist` returns a clean
  error and the agent keeps working on qwen3 + escalation.
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
