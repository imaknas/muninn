#!/usr/bin/env python3
"""The web API contract (contracts/web-api.v1.schema.json), generated from the pages' own
declarations of what the server answers (src/muninn/adapters/web/ts/types.ts).

types.ts uses a small part of TypeScript: interfaces and type aliases made of strings, numbers,
booleans, null, unknown, string literals, unions, arrays, tuples, inline object types, index
signatures, and the generics List<T>, Record<K, V> and Partial<T>. Anything else is an error, so a
type the contract cannot express is noticed rather than left out.

Each interface becomes a $def. An answer may carry more fields than it declares (new fields can be
added); a declared field that is not optional (`?`) must be there, and every field has its type.
The routes are the `Api` interface: "METHOD /path" to the type of a successful answer.

    uv run python bin/web_api_schema.py          # writes the contract
    uv run python bin/web_api_schema.py --check  # fails when it is out of date
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TYPES = ROOT / "src" / "muninn" / "adapters" / "web" / "ts" / "types.ts"
CONTRACT = ROOT / "contracts" / "web-api.v1.schema.json"

TOKEN = re.compile(r'\s*(?:(//[^\n]*)|("(?:[^"\\]|\\.)*")|([A-Za-z_][A-Za-z0-9_]*)|(\d+)|(\S))')
GENERIC = {"List", "Record", "Partial"}


def tokens(text: str) -> list[str]:
    out = []
    for m in TOKEN.finditer(text):
        if m.group(1):
            continue  # a comment
        tok = next(g for g in m.groups()[1:] if g is not None)
        out.append(tok)
    return out


class Parser:
    def __init__(self, toks: list[str]):
        self.t = toks
        self.i = 0
        self.defs: dict[str, dict[str, Any]] = {}
        self.generic_params: dict[str, list[str]] = {}

    def peek(self, k: int = 0) -> str | None:
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self, want: str | None = None) -> str:
        tok = self.peek()
        if tok is None or (want is not None and tok != want):
            raise SyntaxError(f"expected {want!r}, got {tok!r} near {' '.join(self.t[max(0, self.i - 6):self.i + 6])}")
        self.i += 1
        return tok

    def file(self) -> None:
        while self.peek() is not None:
            self.take("export")
            kind = self.take()
            name = self.take()
            params = self.params()
            if kind == "interface":
                self.defs[name] = self.object()
            elif kind == "type":
                self.take("=")
                self.defs[name] = self.union()
                self.take(";")
            else:
                raise SyntaxError(f"unsupported declaration {kind!r}")
            if params:
                self.generic_params[name] = params

    def params(self) -> list[str]:
        if self.peek() != "<":
            return []
        self.take("<")
        names = [self.take()]
        while self.peek() == ",":
            self.take(",")
            names.append(self.take())
        self.take(">")
        return names

    def object(self) -> dict[str, Any]:
        self.take("{")
        props: dict[str, Any] = {}
        required: list[str] = []
        extra: dict[str, Any] | None = None
        while self.peek() != "}":
            if self.peek() == "[":  # an index signature: [k: string]: T
                self.take("[")
                self.take()
                self.take(":")
                self.take("string")
                self.take("]")
                self.take(":")
                extra = self.union()
            else:
                key = self.take()
                key = json.loads(key) if key.startswith('"') else key
                optional = self.peek() == "?"
                if optional:
                    self.take("?")
                self.take(":")
                props[key] = self.union()
                if not optional:
                    required.append(key)
            if self.peek() in (";", ","):
                self.take()
        self.take("}")
        out: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            out["required"] = required
        if extra is not None and extra != {}:
            out["additionalProperties"] = extra
        return out

    def union(self) -> dict[str, Any]:
        if self.peek() == "|":
            self.take("|")
        parts = [self.postfix()]
        while self.peek() == "|":
            self.take("|")
            parts.append(self.postfix())
        if len(parts) == 1:
            return parts[0]
        literals = [p["const"] for p in parts if set(p) == {"const"}]
        if len(literals) == len(parts):
            return {"enum": literals}
        if any(p == {} for p in parts):
            return {}  # unknown | anything is anything
        return {"anyOf": parts}

    def postfix(self) -> dict[str, Any]:
        t = self.primary()
        while self.peek() == "[" and self.peek(1) == "]":
            self.take("[")
            self.take("]")
            t = {"type": "array", "items": t}
        return t

    def primary(self) -> dict[str, Any]:
        tok = self.peek()
        if tok == "{":
            return self.object()
        if tok == "(":
            self.take("(")
            t = self.union()
            self.take(")")
            return t
        if tok == "[":  # a tuple
            self.take("[")
            items = [self.union()]
            while self.peek() == ",":
                self.take(",")
                items.append(self.union())
            self.take("]")
            return {"type": "array", "prefixItems": items, "minItems": len(items), "maxItems": len(items)}
        self.take()
        if tok.startswith('"'):
            return {"const": json.loads(tok)}
        if tok in ("string", "number", "boolean", "null"):
            return {"type": tok}
        if tok == "unknown":
            return {}
        if tok in GENERIC:
            self.take("<")
            args = [self.union()]
            while self.peek() == ",":
                self.take(",")
                args.append(self.union())
            self.take(">")
            return self.generic(tok, args)
        if self.peek() == "<":
            raise SyntaxError(f"unsupported generic {tok}<…>")
        return {"$ref": f"#/$defs/{tok}"}

    def generic(self, name: str, args: list[dict[str, Any]]) -> dict[str, Any]:
        if name == "List":
            return {"type": "object", "properties": {"items": {"type": "array", "items": args[0]}}, "required": ["items"]}
        if name == "Record":
            keys, value = args
            if "enum" in keys:  # a closed set of keys
                return {"type": "object", "properties": {k: value for k in keys["enum"]}, "required": list(keys["enum"])}
            return {"type": "object", "additionalProperties": value}
        if name == "Partial":
            inner = dict(args[0])
            inner.pop("required", None)
            return inner
        raise SyntaxError(name)


def contract() -> dict[str, Any]:
    p = Parser(tokens(TYPES.read_text(encoding="utf-8")))
    p.file()
    api = p.defs.pop("Api")
    p.defs.pop("List", None)  # a generic: expanded where it is used
    routes = api["properties"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://muninn.local/contracts/web-api.v1.schema.json",
        "title": "Muninn web API v1",
        "description": "What each route the pages call answers when it succeeds; any 4xx answers ErrorReply. Generated from "
                       "src/muninn/adapters/web/ts/types.ts by bin/web_api_schema.py: edit that file, not this one. "
                       "An answer may carry fields it does not declare.",
        "routes": {route: routes[route] for route in routes},
        "$defs": dict(sorted(p.defs.items())),
    }


def render() -> str:
    return json.dumps(contract(), ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not CONTRACT.is_file() or CONTRACT.read_text(encoding="utf-8") != text:
            print(f"{CONTRACT.relative_to(ROOT)} is out of date: run uv run python bin/web_api_schema.py", file=sys.stderr)
            return 1
        return 0
    CONTRACT.write_text(text, encoding="utf-8")
    print(f"wrote {CONTRACT.relative_to(ROOT)} ({len(contract()['routes'])} routes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
