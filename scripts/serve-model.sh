#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
VLLM_BIN="${VLLM_BIN:-.venv/bin/vllm}"
MODEL_PATH="${MODEL_PATH:-models/Qwen3.5-9B}"
MODEL_NAME="${LLM_MODEL:-Qwen3.5-9B}"
TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-2}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-16384}"

if [[ ! -x "$VLLM_BIN" ]]; then
  echo "vLLM not found at $VLLM_BIN; install it in .venv first." >&2
  exit 1
fi

exec "$VLLM_BIN" serve "$MODEL_PATH" \
  --served-model-name "$MODEL_NAME" --host 127.0.0.1 --port 8001 \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" --max-model-len "$MAX_MODEL_LEN" --gpu-memory-utilization 0.8 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 --enforce-eager
