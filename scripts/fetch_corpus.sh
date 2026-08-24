#!/usr/bin/env bash
# Shallow clones of public CTF knowledge. Small ones first so ingest dev can start.
set -u
C=~/ctf-agent/corpus
L=~/ctf-agent/logs/corpus.log
clone(){ # url dir
  local url="$1" dir="$2"
  if [ -d "$C/$dir/.git" ]; then
    echo "[$(date +%T)] update $dir" >>"$L"
    git -C "$C/$dir" pull --depth 1 -q >>"$L" 2>&1
  else
    echo "[$(date +%T)] clone $dir" >>"$L"
    git clone --depth 1 -q "$url" "$C/$dir" >>"$L" 2>&1
  fi
  echo "[$(date +%T)] done $dir ($(du -sh $C/$dir 2>/dev/null|cut -f1))" >>"$L"
}
# small reference bases first
clone https://github.com/GTFOBins/GTFOBins.github.io.git       gtfobins
clone https://github.com/swisskyrepo/PayloadsAllTheThings.git  payloads
clone https://github.com/HackTricks-wiki/hacktricks.git        hacktricks
# writeup collections
clone https://github.com/p4-team/ctf.git                       p4-ctf
clone https://github.com/perfectblue/ctf-writeups.git          perfectblue
clone https://github.com/sajjadium/ctf-archives.git            ctf-archives
echo "[$(date +%T)] CORPUS DONE" >>"$L"
