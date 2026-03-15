#!/usr/bin/env bash
# Nightly CrossCodeEval benchmark — run after code changes to catch regressions.
# Cost: ~$0.20 on gpt-4o-mini for 50 examples.
# Usage: bash bench/nightly.sh [model]
set -euo pipefail

cd "$(dirname "$0")/.."

MODEL="${1:-qwen2.5-coder:32b}"
DATE=$(date +%Y-%m-%d)
OUTPUT="bench/results/nightly_${DATE}.jsonl"

pip install -e ".[bench]" -q 2>/dev/null

echo "Running CrossCodeEval (50 examples, model=${MODEL})..."
python3 -m bench.crosscode.run \
  --real-repos \
  --subset 50 \
  --conditions no_context,tempograph \
  --model "$MODEL" \
  --output "$OUTPUT"

echo ""
echo "Results saved to ${OUTPUT}"
echo ""
python3 -m bench.report
