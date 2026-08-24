#!/usr/bin/env bash
L=~/ctf-agent/logs/embed.log
echo "[$(date +%T)] bge-m3 (safetensors) start" >> $L
~/miniforge3/bin/hf download BAAI/bge-m3 \
  model.safetensors config.json sentencepiece.bpe.model tokenizer.json \
  tokenizer_config.json special_tokens_map.json config_sentence_transformers.json \
  sentence_bert_config.json modules.json 1_Pooling/config.json >> $L 2>&1
echo "[$(date +%T)] bge-m3 done" >> $L
echo "[$(date +%T)] reranker start" >> $L
~/miniforge3/bin/hf download BAAI/bge-reranker-v2-m3 \
  model.safetensors config.json sentencepiece.bpe.model tokenizer.json \
  tokenizer_config.json special_tokens_map.json >> $L 2>&1
echo "[$(date +%T)] EMBED DONE" >> $L
