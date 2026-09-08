#!/usr/bin/env python3
"""
Real-runtime schema probe.

`tools/check_schema.py` is a static (AST) diagnostic. This probe is the ground
truth: it imports the contract with the *actual* GenLayer python standard
library (the same code the node runs) and calls `get_schema` on the contract
class. If this passes, the node can build the contract ABI; if it fails you get
the exact Python traceback instead of Studio's opaque
"could not load contract schema".

The SDK is taken from a local checkout of https://github.com/genlayerlabs/genvm
(tag `v0.2.16` by default):

    python3 tools/schema_probe.py --sdk /path/to/genvm/runners/genlayer-py-std/src
    python3 tools/schema_probe.py            # clones the tag into .cache/ if needed

Notes on why a fake runtime is needed:
  * `genlayer.gl` imports `_genlayer_wasi`; a stub with `FAKE_VM = True` is enough
    for schema generation (no storage or consensus call is made).
  * the raw transaction message is read from stdin as calldata, so the probe
    encodes a synthetic message (exactly what the node does for `#get-schema`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "fact_evolution.py"
CACHE = ROOT / ".cache" / "genvm"
DEFAULT_TAG = "v0.2.16"
SDK_SUBPATH = "runners/genlayer-py-std/src"

WASI_STUB = """FAKE_VM = True


def _unavailable(*args, **kwargs):
    raise RuntimeError('GenVM host call is unavailable in the schema probe')


gl_call = _unavailable
get_balance = _unavailable
storage_read = _unavailable
storage_write = _unavailable
"""

CHILD = r'''
import json
import sys

sys.path.insert(0, SDK)
sys.path.insert(0, str(CONTRACT_DIR))

import importlib

module = importlib.import_module(CONTRACT_MODULE)

try:
    from genlayer.py.get_schema import get_schema           # v0.2.x
except ImportError:
    from genlayer._internal.get_schema import get_schema    # newer runners

import genlayer

contracts = [
    value
    for value in vars(module).values()
    if isinstance(value, type)
    and value.__module__ == module.__name__
    and any(base.__name__ == "Contract" for base in value.__mro__[1:])
]
if len(contracts) != 1:
    print("FAIL: expected exactly one gl.Contract subclass, found", len(contracts))
    sys.exit(1)

schema = get_schema(contracts[0])
methods = schema["methods"]
print("contract:", contracts[0].__name__)
print("ctor:", json.dumps(schema["ctor"]))
print("methods:", len(methods))
for name in sorted(methods):
    m = methods[name]
    kind = "view " if m.get("readonly") else "write"
    params = ", ".join(f"{p}: {json.dumps(t)}" for p, t in m["params"])
    print(f"  {kind} {name}({params}) -> {json.dumps(m['ret'])}")
'''


def ensure_sdk(sdk: str | None, tag: str) -> pathlib.Path:
    if sdk:
        return pathlib.Path(sdk).resolve()
    target = CACHE / tag
    src = target / SDK_SUBPATH
    if src.is_dir():
        return src
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"cloning genvm {tag} into {target} ...")
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "https://github.com/genlayerlabs/genvm",
            str(target),
        ],
        check=True,
    )
    return src


def encode_message(sdk: pathlib.Path, out: pathlib.Path) -> None:
    script = f"""
import sys
sys.path.insert(0, {str(sdk)!r})
import genlayer.py.calldata as calldata
from genlayer.py.types import Address

message = {{
    "contract_address": Address(b"\\x11" * 20),
    "sender_address": Address(b"\\x22" * 20),
    "origin_address": Address(b"\\x22" * 20),
    "stack": [],
    "value": 0,
    "datetime": "2026-01-01T00:00:00+00:00",
    "is_init": False,
    "chain_id": 1,
    "entry_kind": 0,
}}
open({str(out)!r}, "wb").write(calldata.encode(message))
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", default=None, help="path to runners/genlayer-py-std/src")
    parser.add_argument("--tag", default=DEFAULT_TAG, help="genvm tag to clone when --sdk is absent")
    parser.add_argument("contract", nargs="?", default=str(CONTRACT))
    args = parser.parse_args()

    sdk = ensure_sdk(args.sdk, args.tag)
    if not (sdk / "genlayer").is_dir():
        print(f"FAIL: {sdk} does not look like the genlayer python std library")
        return 2

    contract = pathlib.Path(args.contract).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        # writable copy of the SDK so the wasi stub can sit next to it
        stub_dir = tmpdir / "stub"
        stub_dir.mkdir()
        (stub_dir / "_genlayer_wasi.py").write_text(WASI_STUB)

        message = tmpdir / "message.bin"
        encode_message(sdk, message)

        child = tmpdir / "probe_child.py"
        child.write_text(
            f"SDK = {str(sdk)!r}\n"
            f"CONTRACT_DIR = {str(contract.parent)!r}\n"
            f"CONTRACT_MODULE = {contract.stem!r}\n"
            f"import sys; sys.path.insert(0, {str(stub_dir)!r})\n" + CHILD
        )

        with message.open("rb") as stdin:
            result = subprocess.run([sys.executable, str(child)], stdin=stdin)

    if result.returncode != 0:
        print(f"\nFAIL {contract.name}: the runtime could not build the ABI (see traceback above)")
        return 1
    print(f"\nPASS {contract.name}: ABI built by the real GenLayer std library ({args.tag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
