#!/usr/bin/env bash
# Generate SHA256SUMS over every file in the pack except itself,
# the git directory, caches, and the local (non-repo) submission text.
set -u
cd "$(dirname "$0")/.." || exit 1
find . -type f \
  -not -path './.git/*' \
  -not -name 'SHA256SUMS' \
  -not -name 'submission-text-final.txt' \
  -not -path '*__pycache__*' \
  | LC_ALL=C sort | xargs sha256sum > SHA256SUMS
echo "wrote SHA256SUMS ($(wc -l < SHA256SUMS) files)"
