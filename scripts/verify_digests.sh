#!/usr/bin/env bash
# Verify every digest in SHA256SUMS.
set -u
cd "$(dirname "$0")/.." || exit 1
sha256sum -c SHA256SUMS
