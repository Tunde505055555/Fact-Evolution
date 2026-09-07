"""
Off-chain harness for the deterministic half of the contract.

The GenVM SDK is not importable outside the runtime, so we install a minimal
stub named `genlayer` that mirrors the *real* v0.2.16 surface the contract uses:

  * `gl.Contract` — the base class every contract must inherit from
  * `gl.public.write` / `gl.public.view` decorators
  * `gl.eq_principle.prompt_comparative(fn, principle)`
  * `gl.message.sender_address`, `gl.message_raw["datetime"]`
  * `TreeMap` (`get`, `get_or_insert_default`), `DynArray`, `u256`, `Address`

Non-deterministic paths (`gl.nondet.*`, equivalence principles) are NOT emulated
here — those belong in GenLayer Studio / genlayer-test runs, not unit tests.
`tools/schema_probe.py` validates the ABI against the genuine SDK.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contracts"))


def _install_stub() -> None:
    if "genlayer" in sys.modules:
        return

    mod = types.ModuleType("genlayer")

    class _Web:
        @staticmethod
        def render(url, mode="text"):
            raise RuntimeError("non-deterministic block not available off chain")

    class _Nondet:
        web = _Web()

        @staticmethod
        def exec_prompt(prompt, **kwargs):
            raise RuntimeError("non-deterministic block not available off chain")

    class _EqPrinciple:
        @staticmethod
        def prompt_comparative(fn, principle=""):
            return fn()

        @staticmethod
        def strict_eq(fn):
            return fn()

    def _identity(fn):
        return fn

    class _Write:
        payable = staticmethod(_identity)

        def __call__(self, fn):
            return fn

    class _Public:
        view = staticmethod(_identity)
        write = _Write()

    class _Message:
        contract_address = "0xcontract"
        sender_address = "0xsender"
        origin_address = "0xsender"
        value = 0
        chain_id = 1

    class _DynArray(list):
        pass

    class _TreeMap(dict):
        def get_or_insert_default(self, key):
            if key not in self:
                self[key] = _DynArray()
            return self[key]

    class _Contract:
        pass

    class _GL:
        Contract = _Contract
        nondet = _Nondet()
        eq_principle = _EqPrinciple()
        public = _Public()
        message = _Message()
        message_raw = {"datetime": "2026-01-01T00:00:00+00:00"}

    mod.gl = _GL()
    mod.TreeMap = _TreeMap
    mod.DynArray = _DynArray
    mod.Array = list
    mod.u256 = int
    mod.i256 = int
    mod.bigint = int
    mod.Address = str
    mod.__all__ = [
        "gl",
        "TreeMap",
        "DynArray",
        "Array",
        "u256",
        "i256",
        "bigint",
        "Address",
    ]
    sys.modules["genlayer"] = mod


_install_stub()
