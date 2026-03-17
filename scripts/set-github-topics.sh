#!/usr/bin/env bash
# Set GitHub repository topics for heggria/Hermit
set -euo pipefail

gh repo edit heggria/Hermit \
  --add-topic agent \
  --add-topic ai-agent \
  --add-topic governance \
  --add-topic llm \
  --add-topic mcp \
  --add-topic local-first \
  --add-topic kernel \
  --add-topic python \
  --add-topic cli \
  --add-topic automation \
  --add-topic ai

echo "Topics set. Verifying..."
gh repo view heggria/Hermit --json repositoryTopics --jq '.repositoryTopics[].name'
