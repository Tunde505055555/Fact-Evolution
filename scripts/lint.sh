#!/usr/bin/env bash
# Contract lint gate: byte-compile, schema diagnostic, genvm lint (when available), tests.
set -uo pipefail

cd "$(dirname "$0")/.."
CONTRACT="contracts/fact_evolution.py"
status=0

echo "==> python compile"
python3 -m py_compile "$CONTRACT" || status=1

echo "==> schema diagnostic (static, GenVM v0.2.16 calldata safety)"
python3 tools/check_schema.py "$CONTRACT" || status=1

echo "==> schema probe (real genlayer std library builds the ABI)"
if [ -n "${GENLAYER_SDK_SRC:-}" ]; then
  python3 tools/schema_probe.py --sdk "$GENLAYER_SDK_SRC" || status=1
elif [ -d ".cache/genvm/v0.2.16/runners/genlayer-py-std/src" ] || [ "${SCHEMA_PROBE_CLONE:-0}" = "1" ]; then
  python3 tools/schema_probe.py || status=1
else
  echo "skipped: no local SDK. Set GENLAYER_SDK_SRC=<genvm>/runners/genlayer-py-std/src"
  echo "         or run with SCHEMA_PROBE_CLONE=1 to fetch it into .cache/."
fi


echo "==> genvm lint"
if command -v genvm >/dev/null 2>&1; then
  genvm lint "$CONTRACT" || status=1
elif command -v genlayer >/dev/null 2>&1; then
  genlayer contract lint "$CONTRACT" || status=1
else
  echo "skipped: neither \`genvm\` nor \`genlayer\` CLI found on PATH."
  echo "         install the GenLayer CLI and re-run to lint against the node's own parser."
fi

echo "==> tests"
if python3 -c "import pytest" >/dev/null 2>&1; then
  python3 -m pytest tests -q || status=1
else
  echo "skipped: pytest not installed (pip install pytest)"
fi

if [ "$status" -eq 0 ]; then
  echo "==> OK"
else
  echo "==> FAILED"
fi
exit "$status"
