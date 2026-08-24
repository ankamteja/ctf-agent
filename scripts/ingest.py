#!/usr/bin/env python3
"""Ingest CTF corpus -> chromadb. Embeddings on CPU (frees GPU for the LLM)."""
import os, sys, hashlib, re, argparse, time
from pathlib import Path

CORPUS = Path.home()/"ctf-agent"/"corpus"
STORE  = Path.home()/"ctf-agent"/"store"
COLL   = "ctf"
EMB_MODEL = "BAAI/bge-m3"
EXTS = {".md",".markdown",".txt",".rst",".py",".c",".sh"}
SKIP_DIRS = {".git","node_modules","assets","images","img",".github"}
MAXBYTES = 400_000   # skip giant files
CHUNK = 1200         # chars
OVERLAP = 200

def iter_files(root):
    for p in root.rglob("*"):
        if p.is_dir(): continue
        if any(s in p.parts for s in SKIP_DIRS): continue
        if p.suffix.lower() not in EXTS: continue
        try:
            if p.stat().st_size > MAXBYTES: continue
        except OSError: continue
        yield p

def chunk_text(t):
    t = re.sub(r"\n{3,}","\n\n",t)
    out=[]; i=0; n=len(t)
    while i < n:
        out.append(t[i:i+CHUNK]); i += CHUNK-OVERLAP
    return [c for c in out if len(c.strip())>60]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--reset",action="store_true")
    ap.add_argument("--batch",type=int,default=64)
    ap.add_argument("--limit",type=int,default=0,help="max files (0=all), for smoke test")
    a=ap.parse_args()

    import chromadb
    from sentence_transformers import SentenceTransformer
    print(f"[ingest] loading {EMB_MODEL} on CPU ...",flush=True)
    model=SentenceTransformer(EMB_MODEL,device="cpu")
    client=chromadb.PersistentClient(path=str(STORE))
    if a.reset:
        try: client.delete_collection(COLL)
        except Exception: pass
    coll=client.get_or_create_collection(COLL,metadata={"hnsw:space":"cosine"})

    files=list(iter_files(CORPUS))
    if a.limit: files=files[:a.limit]
    print(f"[ingest] {len(files)} files",flush=True)

    docs=[];metas=[];ids=[];seen=set();t0=time.time();nchunks=0
    def flush():
        nonlocal docs,metas,ids
        if not docs: return
        emb=model.encode(docs,batch_size=a.batch,normalize_embeddings=True,
                         show_progress_bar=False).tolist()
        coll.add(documents=docs,embeddings=emb,metadatas=metas,ids=ids)
        docs,metas,ids=[],[],[]
    for fi,p in enumerate(files):
        try: txt=p.read_text(errors="ignore")
        except Exception: continue
        rel=str(p.relative_to(CORPUS))
        src=rel.split("/")[0]
        for ci,ch in enumerate(chunk_text(txt)):
            h=hashlib.md5((rel+str(ci)+ch[:40]).encode()).hexdigest()
            if h in seen: continue
            seen.add(h)
            docs.append(ch); ids.append(h)
            metas.append({"path":rel,"source":src,"chunk":ci})
            nchunks+=1
            if len(docs)>=256: flush()
        if fi%200==0:
            flush()
            print(f"  {fi}/{len(files)} files, {nchunks} chunks, {time.time()-t0:.0f}s",flush=True)
    flush()
    print(f"[ingest] DONE {nchunks} chunks in {time.time()-t0:.0f}s. total={coll.count()}",flush=True)

if __name__=="__main__": main()
