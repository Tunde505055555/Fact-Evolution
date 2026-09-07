#!/usr/bin/env python3
"""
Deploy `contracts/fact_evolution.py` to GenLayer Studio (or any GenLayer RPC endpoint).

The schema diagnostic runs first — a contract that fails it will fail schema generation on
the node, so deployment is refused locally instead of producing an opaque node error.

Usage:
  python3 scripts/deploy.py --studio http://localhost:4000 [--account 0x...] [--dry-run]

With no RPC reachable, `--dry-run` still prints the exact JSON-RPC payload so the contract
can be deployed from the Studio UI or any other tooling.
"""

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "fact_evolution.py"


def run_schema_check() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_schema.py"), str(CONTRACT)],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print("\nAborting: fix the schema issues above before deploying.")
        sys.exit(1)


def rpc(url: str, method: str, params: list) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--studio", default="http://localhost:4000", help="Studio/RPC base URL")
    parser.add_argument("--account", default="", help="deployer account address")
    parser.add_argument("--dry-run", action="store_true", help="print the payload, do not send")
    args = parser.parse_args()

    run_schema_check()

    code = CONTRACT.read_bytes()
    encoded = base64.b64encode(code).decode()
    endpoint = args.studio.rstrip("/") + "/api"
    params = [
        {
            "contract_code_b64": encoded,
            "constructor_args": {},
            "from_account": args.account or None,
        }
    ]

    print(f"\ncontract: {CONTRACT.relative_to(ROOT)} ({len(code)} bytes)")
    print(f"endpoint: {endpoint}")
    print(f"method:   gen_deployContract")

    if args.dry_run:
        print("\n--dry-run payload:")
        print(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "gen_deployContract", "params": params}, indent=2)[:2000])
        return 0

    try:
        response = rpc(endpoint, "gen_deployContract", params)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"\nRPC unreachable ({exc}). Re-run with --dry-run and deploy the payload manually,")
        print("or start GenLayer Studio and point --studio at it.")
        return 2

    print("\nresponse:")
    print(json.dumps(response, indent=2)[:4000])
    return 0 if "result" in response else 1


if __name__ == "__main__":
    sys.exit(main())
