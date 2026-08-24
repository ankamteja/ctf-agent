#!/usr/bin/env bash
L=~/ctf-agent/logs
# wait for gpt-oss pull to finish (ollama serializes, but be explicit)
until ollama list 2>/dev/null | grep -q gpt-oss; do sleep 60; done
echo "[$(date +%T)] gpt-oss present, pulling Qwen3-30B-A3B" >> $L/heavy.log
ollama pull qwen3:30b-a3b >> $L/heavy.log 2>&1
echo "[$(date +%T)] HEAVY DONE" >> $L/heavy.log
