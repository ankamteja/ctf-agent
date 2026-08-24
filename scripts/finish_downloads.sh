#!/usr/bin/env bash
L=~/ctf-agent/logs/finish.log; C=~/ctf-agent/corpus
export GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=60
git config --global http.version HTTP/1.1
git config --global http.postBuffer 524288000
echo "[$(date +%T)] START" >> $L
# embedder (plain, resumes from cache)
for repo in BAAI/bge-m3 BAAI/bge-reranker-v2-m3; do
  n=0; until ~/miniforge3/bin/hf download "$repo" >> $L 2>&1; do
    n=$((n+1)); [ $n -ge 10 ] && { echo "[$(date +%T)] GIVEUP $repo" >>$L; break; }
    echo "[$(date +%T)] retry $repo $n" >> $L; sleep 15; done
  echo "[$(date +%T)] embed ok $repo" >> $L
done
# corpus repos that failed
reclone(){ local url="$1" d="$2" n=0; [ -d "$C/$d/.git" ] && { echo "[$(date +%T)] have $d" >>$L; return; }
  rm -rf "$C/$d"; until git clone --depth 1 -q "$url" "$C/$d" 2>>$L; do
    n=$((n+1)); [ $n -ge 8 ] && { echo "[$(date +%T)] GIVEUP $d" >>$L; return; }
    rm -rf "$C/$d"; echo "[$(date +%T)] retry $d $n" >>$L; sleep 15; done
  echo "[$(date +%T)] corpus ok $d $(du -sh $C/$d|cut -f1)" >> $L; }
reclone https://github.com/HackTricks-wiki/hacktricks.git   hacktricks
reclone https://github.com/p4-team/ctf.git                  p4-ctf
reclone https://github.com/perfectblue/ctf-writeups.git     perfectblue
reclone https://github.com/sajjadium/ctf-archives.git       ctf-archives
echo "[$(date +%T)] FINISH DONE" >> $L
