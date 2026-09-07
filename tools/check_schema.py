#!/usr/bin/env python3
"""
Schema parser diagnostic for GenLayer Intelligent Contracts (GenVM v0.2.16).

Why this exists
---------------
The Studio / GenVM schema parser walks the AST of a contract and derives the
ABI from the *annotations* of every `@gl.public.*` method. It fails (often with
an opaque "schema" error) when it meets a type it cannot map onto calldata.
This script reproduces that walk locally so schema problems surface before
deployment.

Rules enforced (v0.2.16 schema-safe subset):
  * every public method annotates all params and its return type
  * param/return annotations are limited to calldata-native primitives
  * default values are plain literals
  * `self` only, no *args/**kwargs on public methods
  * storage fields use only str/bool/u256/i256/bigint/Address/TreeMap/DynArray
  * exactly one `@gl.contract` class, `__init__` annotated `-> None`

Usage:  python3 tools/check_schema.py contracts/fact_evolution.py
"""

import ast
import sys

CALLDATA_PRIMITIVES = {"str", "int", "bool", "bytes", "u256", "i256", "bigint", "Address", "None"}
STORAGE_SCALARS = {"str", "bool", "u256", "i256", "bigint", "Address", "bytes"}
STORAGE_GENERICS = {"TreeMap", "DynArray"}

PUBLIC_DECORATORS = {"gl.public.write", "gl.public.view", "gl.public.write.payable"}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Subscript):
        return f"{dotted(node.value)}[...]"
    return type(node).__name__


def ann_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return dotted(node)


def check_calldata_annotation(node: ast.AST, where: str, problems: list) -> None:
    if node is None:
        problems.append(f"{where}: missing type annotation (schema parser cannot infer it)")
        return
    if isinstance(node, ast.Constant) and node.value is None:
        return
    if isinstance(node, ast.Name):
        if node.id not in CALLDATA_PRIMITIVES:
            problems.append(
                f"{where}: `{node.id}` is not calldata-native. Use one of "
                f"{sorted(CALLDATA_PRIMITIVES)} (encode rich payloads as JSON `str`)."
            )
        return
    if isinstance(node, ast.Subscript):
        problems.append(
            f"{where}: generic annotation `{ann_text(node)}` — generics/Optional/unions are the "
            "single most common cause of schema parse failures. Pass a JSON `str` instead."
        )
        return
    if isinstance(node, ast.BinOp):
        problems.append(f"{where}: union annotation `{ann_text(node)}` is not schema-safe.")
        return
    problems.append(f"{where}: unsupported annotation `{ann_text(node)}`.")


def check_storage_annotation(node: ast.AST, where: str, problems: list) -> None:
    if isinstance(node, ast.Name):
        if node.id not in STORAGE_SCALARS:
            problems.append(f"{where}: storage type `{node.id}` is not a supported storage scalar.")
        return
    if isinstance(node, ast.Subscript):
        base = dotted(node.value)
        if base not in STORAGE_GENERICS:
            problems.append(f"{where}: storage generic `{base}[...]` unsupported (use TreeMap/DynArray).")
            return
        args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for arg in args:
            check_storage_annotation(arg, where, problems)
        return
    problems.append(f"{where}: unsupported storage annotation `{ann_text(node)}`.")


def main(path: str) -> int:
    source = open(path, "r", encoding="utf-8").read()
    problems: list = []
    notes: list = []

    head = source.splitlines()[:3]
    if not any(line.startswith("# { ") for line in head):
        problems.append(
            "missing GenVM runner directive comment in the first lines, e.g. "
            '# { "Depends": "py-genlayer:<hash>" }'
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print(f"FAIL {path}: syntax error at line {exc.lineno}: {exc.msg}")
        return 1

    # Removed / renamed runtime APIs: using one of these raises at *import* time,
    # which is what the node reports as "could not load contract schema".
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if dotted(dec) == "gl.contract":
                    problems.append(
                        f"line {node.lineno}: `@gl.contract` no longer exists — a contract must "
                        "inherit the base class: `class X(gl.Contract):`"
                    )
        if isinstance(node, ast.Attribute):
            name = dotted(node)
            if name.startswith("gl.block"):
                problems.append(
                    f"line {node.lineno}: `{name}` does not exist; transaction time comes from "
                    "`gl.message_raw['datetime']`"
                )
            if name == "gl.eq_principle_prompt_comparative":
                problems.append(
                    f"line {node.lineno}: use `gl.eq_principle.prompt_comparative(fn, principle)`"
                )
            if name == "gl.eq_principle_strict_eq":
                problems.append(f"line {node.lineno}: use `gl.eq_principle.strict_eq(fn)`")

    def _is_contract_base(node: ast.AST) -> bool:
        return dotted(node).split(".")[-1] == "Contract"

    contracts = [
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and any(_is_contract_base(b) for b in n.bases)
    ]
    if len(contracts) != 1:
        problems.append(
            f"expected exactly one class inheriting `gl.Contract`, found {len(contracts)}"
        )
        print_report(path, problems, notes)
        return 1 if problems else 0

    cls = contracts[0]
    public_methods = 0


    for item in cls.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            check_storage_annotation(
                item.annotation, f"storage `{item.target.id}` (line {item.lineno})", problems
            )
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {dotted(d) for d in item.decorator_list}
        is_public = bool(decorators & PUBLIC_DECORATORS)
        where_base = f"{cls.name}.{item.name} (line {item.lineno})"

        if item.name == "__init__":
            if item.returns is None or ann_text(item.returns) != "None":
                problems.append(f"{where_base}: __init__ must be annotated `-> None`")
            continue
        if not is_public:
            if not item.name.startswith("_"):
                notes.append(f"{where_base}: non-public method without leading underscore (not exported)")
            continue

        public_methods += 1
        args = item.args
        if args.vararg or args.kwarg:
            problems.append(f"{where_base}: *args/**kwargs are not representable in the ABI")
        if not args.args or args.args[0].arg != "self":
            problems.append(f"{where_base}: first parameter must be `self`")
        for arg in args.args[1:] + args.kwonlyargs:
            check_calldata_annotation(arg.annotation, f"{where_base} param `{arg.arg}`", problems)
        for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            if not isinstance(default, ast.Constant):
                problems.append(
                    f"{where_base}: default `{ann_text(default)}` is not a literal; the schema parser "
                    "only serializes constant defaults"
                )
        check_calldata_annotation(item.returns, f"{where_base} return", problems)
        if "gl.public.view" in decorators:
            mutations = [
                n
                for n in ast.walk(item)
                if isinstance(n, (ast.Assign, ast.AugAssign))
                and any(
                    isinstance(t, ast.Attribute) and dotted(t).startswith("self.")
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                )
            ]
            if mutations:
                problems.append(f"{where_base}: view method writes to storage (line {mutations[0].lineno})")

    notes.append(f"{public_methods} public entrypoints parsed from `{cls.name}`")
    print_report(path, problems, notes)
    return 1 if problems else 0


def print_report(path: str, problems: list, notes: list) -> None:
    for n in notes:
        print(f"note: {n}")
    if problems:
        print(f"\nFAIL {path}: {len(problems)} schema issue(s)")
        for p in problems:
            print(f"  - {p}")
    else:
        print(f"\nPASS {path}: schema is v0.2.16 calldata-safe")


if __name__ == "__main__":
    targets = sys.argv[1:] or ["contracts/fact_evolution.py"]
    sys.exit(max(main(t) for t in targets))
