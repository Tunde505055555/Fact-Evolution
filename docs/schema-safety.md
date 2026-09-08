# Schema safety and the schema parser (GenVM v0.2.16)

## How the schema parser works

When a contract is deployed or opened in Studio, GenVM derives the contract's ABI by
walking the module AST:

1. it finds the single class decorated with `@gl.contract`;
2. it reads the class-level annotated assignments as the **storage layout**;
3. for every method decorated `@gl.public.view` / `@gl.public.write` it reads each
   parameter annotation, each default value and the return annotation, and maps them onto
   **calldata types**;
4. anything it cannot map aborts schema generation — which surfaces as a generic
   "schema" / "cannot build schema" error with little detail about the offending line.

Because the parser is static, the annotation must be resolvable *syntactically*. A type
that is valid Python but not calldata-native (a `dataclass`, `Optional[str]`, `list[str]`,
`dict`, `Any`, an aliased type, a string forward reference) is exactly the failure class
that produces those opaque errors.

## The safe subset used by this contract

Public entrypoints:

| allowed | avoid |
| --- | --- |
| `str`, `int`, `bool`, `bytes`, `u256`, `i256`, `bigint`, `Address` | `Optional[T]`, `T \| None`, `list[T]`, `dict[..]`, `tuple[..]`, `Any` |
| literal defaults (`""`, `0`, `False`, `"[]"`) | computed or mutable defaults, `None` defaults |
| a single `str` return (JSON text) | dataclass / TypedDict returns, bare `-> None` on views |
| `self` + named params | `*args`, `**kwargs`, keyword-only tricks |

Storage:

| allowed | avoid |
| --- | --- |
| `str`, `bool`, `u256`, `i256`, `bigint`, `Address`, `bytes` | plain `int`, `float`, `set` |
| `TreeMap[str, str]`, `TreeMap[str, DynArray[str]]`, `DynArray[str]` | `@allow_storage` dataclasses (valid but a frequent parse/upgrade hazard) |

Consequences adopted here deliberately:

- **Rich payloads travel as JSON strings.** `submit_claim` takes `sources_json: str = "[]"`
  instead of `list[str]`, and every read returns a JSON envelope string. The contract
  validates and normalizes that JSON on chain, so the ABI stays trivial while the domain
  model stays rich.
- **No dataclasses in storage.** Claims, evaluations, evidence, challenges, relations and
  timeline events are stored as JSON text in `TreeMap` / `DynArray`, which keeps the
  storage schema stable across contract revisions.
- **`__init__` is annotated `-> None`** and initializes every scalar; `TreeMap`/`DynArray`
  fields are implicitly empty.
- **Views never write storage.** `_now()` bumps the logical clock, so it is only called
  from `@gl.public.write` paths.

## Diagnosing schema errors locally

`tools/check_schema.py` reproduces the parser's walk and reports the offending line
instead of a generic failure:

```bash
python3 tools/check_schema.py contracts/fact_evolution.py
# note: 19 public entrypoints parsed from `FactEvolution`
# PASS contracts/fact_evolution.py: schema is v0.2.16 calldata-safe
```

It fails loudly on: missing annotations, non-calldata annotations, generics/unions,
non-literal defaults, `*args`/`**kwargs`, unsupported storage types, views that mutate
storage, a missing runner directive comment on line 1, more or fewer than one
`@gl.contract` class, and an `__init__` not annotated `-> None`.

Checklist when Studio still complains:

1. line 1 must be the runner directive: `# { "Depends": "py-genlayer:test" }`;
2. exactly one `@gl.contract` class in the file;
3. all imports must exist inside GenVM (`json`, `hashlib`, `typing` are fine — no
   third-party packages, no `datetime.now()`, no network libraries);
4. no module-level code that executes at import time beyond constant definitions;
5. re-run `tools/check_schema.py`, then `scripts/lint.sh` (which also runs `genvm lint`
   when the GenVM CLI is on `PATH`).

## Studio "could not load contract schema" — root cause found (2026-09)

The node builds the ABI by **importing the module and reflecting on the contract
class** (`genlayer.py.get_schema`), not by a pure AST walk. So any error raised
while the module is imported surfaces as the generic schema failure. This
contract hit three of them, all now fixed:

| was | now | why |
| --- | --- | --- |
| `@gl.contract class FactEvolution:` | `class FactEvolution(Contract):` where `Contract = gl.Contract` | `gl.contract` is not a decorator in v0.2.16; the base class registers the contract and generates its storage |
| `gl.eq_principle_prompt_comparative(fn, principle=...)` | `gl.eq_principle.prompt_comparative(fn, EQ_PRINCIPLE)` | flat alias does not exist |
| `gl.block.timestamp` | `gl.message_raw["datetime"]` parsed by `parse_iso_seconds` | there is no `gl.block`; time arrives as an ISO string on the message |

Two more runtime-correctness fixes made at the same time:

- `TreeMap[str, DynArray[str]]` values must be created explicitly —
  writes use `get_or_insert_default(key)`, reads use `get(key, None)` so views
  never allocate storage.
- `hashlib` is imported defensively with a pure-python FNV-1a fallback, so the
  module still imports if the runner ships a trimmed stdlib.

## Two-layer validation

1. `tools/check_schema.py` — static house rules plus detection of the removed
   APIs above.
2. `tools/schema_probe.py` — imports the contract against the **real** GenLayer
   python standard library (genvm tag `v0.2.16`) with a stubbed host and calls
   `get_schema`, printing the exact ABI the node will show:

```bash
python3 tools/schema_probe.py            # clones the SDK into .cache/ on first run
python3 tools/schema_probe.py --sdk /path/to/genvm/runners/genlayer-py-std/src
# ... 20 entrypoints, ctor {"params": [], "kwparams": {}}
# PASS fact_evolution.py: ABI built by the real GenLayer std library (v0.2.16)
```

The generated ABI uses only `string` / `int` / `bool` parameters and `string`
returns, which is the safest possible calldata surface.

## Studio "could not load contract schema" — storage layout (2026-09)

The remaining failure was the **persistent storage schema**, not the ABI. Studio's
schema loader reliably supports scalar storage fields; nested containers
(`TreeMap[str, DynArray[str]]`) are the trigger. A known-good deployed contract on
Studio uses exactly one scalar slot plus a pinned runner hash, so this contract now
matches that shape:

```python
# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

class FactEvolution(gl.Contract):
    state_json: str
```

All claims, states, timelines, evidence, challenges, relations and counters live in
that single JSON document. `_Table` / `_Rows` wrappers keep the contract code
unchanged (`self.claims[id] = ...`, `self.timelines.get_or_insert_default(id).append(...)`)
and persist write-through on every mutation, so views still never write storage.
`py-genlayer:test` was also replaced by the pinned runner hash Studio resolves.
