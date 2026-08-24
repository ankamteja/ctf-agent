#!/usr/bin/env bash
until ollama list 2>/dev/null | grep -qi qwen3:8b && ollama list 2>/dev/null | grep -qiE "deephat|whiterabbit"; do sleep 120; done
echo "BOTH MODELS READY $(date +%T)"
