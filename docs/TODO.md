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

## Next, in order 📋

1. [ ] **Ingest + test retrieval** (needs the corpus above)
   ```bash
   python scripts/scan_corpus.py        # safety-scan the new write-ups
   python scripts/ingest.py --reset     # build the search index
   python scripts/retrieve.py "ret2libc with no leak"   # should print matches
   ```
2. [ ] **First end-to-end run** — point the agent at ONE easy challenge folder and
      confirm the whole pipeline works before tuning anything:
   ```bash
   python scripts/agent.py ./path/to/easy_challenge "find the flag"
   ```
3. [ ] **Make the small model smarter (task #5)** — two upgrades:
      - *Constrained tool-calls*: force the model's tool requests into a strict
        JSON shape so it stops malforming them (the #1 local failure).
      - *RAG few-shot*: automatically paste the most similar SOLVED write-up into
        the prompt as a worked example.
4. [ ] **Injection safety test** — plant a fake instruction in a write-up and
      confirm the agent treats it as data and refuses to obey (proves the fencing
      works).
5. [ ] **Web-enum recon tools** — add subfinder / httpx / nuclei / ffuf as agent
      tools for bug-bounty targets. (Use `~/go/bin/httpx`, NOT the python one.)

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
