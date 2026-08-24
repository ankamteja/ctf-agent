#!/usr/bin/env bash
L=~/ctf-agent/logs/models.log
for tag in "qwen3:8b" "DeepHat/DeepHat-V1-7B"; do
  echo "[$(date +%T)] pull $tag" >> $L
  n=0
  until ollama pull "$tag" >> $L 2>&1; do
    n=$((n+1)); [ $n -ge 8 ] && { echo "[$(date +%T)] GIVEUP $tag" >>$L; break; }
    echo "[$(date +%T)] retry $tag ($n)" >> $L; sleep 15
  done
  echo "[$(date +%T)] done $tag" >> $L
done
echo "[$(date +%T)] MODELS DONE" >> $L
