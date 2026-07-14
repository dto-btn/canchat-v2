#!/bin/bash
while true; do
  git checkout --ours backend/requirements.txt uv.lock package.json package-lock.json 2>/dev/null || true
  git add -A 2>/dev/null
  result=$(git rebase --continue 2>&1)
  if echo "$result" | grep -q "No rebase in progress"; then
    break
  fi
  if ! echo "$result" | grep -q "CONFLICT"; then
    break
  fi
done
