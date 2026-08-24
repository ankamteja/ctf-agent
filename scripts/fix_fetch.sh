#!/usr/bin/env bash
# Resilient re-fetch: bge-m3 weights + failed corpus repos. Retries on flaky link.
L=~/ctf-agent/logs/fix.log
C=~/ctf-agent/corpus
export GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=60
git config --global http.postBuffer 524288000
git config --global http.version HTTP/1.1   # avoid HTTP/2 CANCEL drops

echo "[$(date +%T)] bge-m3 weights (retry loop)" >> $L
until ~/miniforge3/bin/hf download BAAI/bge-m3 >> $L 2>&1; do
  echo "[$(date +%T)] bge-m3 retry" >> $L; sleep 10
done
echo "[$(date +%T)] bge-m3 DONE" >> $L

reclone(){ # url dir
  local url="$1" dir="$2" n=0
  rm -rf "$C/$dir"
  until git clone --depth 1 -q "$url" "$C/$dir" 2>>$L; do
    n=$((n+1)); [ $n -ge 6 ] && { echo "[$(date +%T)] GIVEUP $dir" >>$L; return; }
    echo "[$(date +%T)] retry $dir ($n)" >> $L; rm -rf "$C/$dir"; sleep 15
  done
  echo "[$(date +%T)] ok $dir $(du -sh $C/$dir|cut -f1)" >> $L
}
reclone https://github.com/HackTricks-wiki/hacktricks.git       hacktricks
reclone https://github.com/p4-team/ctf.git                      p4-ctf
reclone https://github.com/perfectblue/ctf-writeups.git         perfectblue
echo "[$(date +%T)] FIX DONE" >> $L
