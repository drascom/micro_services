"""Layer rule (README §2.3): `app.core` imports neither `app.control` nor `app.api`/`app.admin`;
`app.control` imports neither `app.api` nor `app.admin`.

AST based (function-level / lazy imports included, relative imports resolved, constant-string
``importlib.import_module``/``__import__`` calls included). Exit 1 and list the violations if any.

usage: python tools/check_imports.py [APP_DIR]     (default: server2/app)
"""
from __future__ import annotations

import ast
import os
import sys

FORBIDDEN = {
    "core": ("app.control", "app.api", "app.admin"),
    "control": ("app.api", "app.admin"),
}


def _module_name(app_dir: str, path: str) -> tuple[str, bool]:
    rel = os.path.relpath(path, os.path.dirname(app_dir))[:-3].replace(os.sep, ".")
    is_pkg = rel.endswith(".__init__")
    if is_pkg:
        rel = rel[: -len(".__init__")]
    return rel, is_pkg


def _resolve(current: str, is_pkg: bool, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = current.split(".")
    base = parts if is_pkg else parts[:-1]
    base = base[: len(base) - (level - 1)] if level > 1 else base
    return ".".join(base + ([module] if module else []))


def _imported(tree: ast.AST, current: str, is_pkg: bool):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            target = _resolve(current, is_pkg, node.level, node.module)
            yield node.lineno, target
            for alias in node.names:
                yield node.lineno, f"{target}.{alias.name}" if target else alias.name
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            arg = node.args[0]
            if name in ("import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield node.lineno, arg.value


def _hits(name: str, prefixes: tuple[str, ...]) -> str | None:
    for p in prefixes:
        if name == p or name.startswith(p + "."):
            return p
    return None


def check(app_dir: str) -> list[str]:
    app_dir = os.path.abspath(app_dir)
    problems: list[str] = []
    for layer, prefixes in FORBIDDEN.items():
        root = os.path.join(app_dir, layer)
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in sorted(files):
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(base, fname)
                current, is_pkg = _module_name(app_dir, path)
                try:
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read(), path)
                except SyntaxError as exc:
                    problems.append(f"{path}: syntax error: {exc}")
                    continue
                seen = set()
                for lineno, name in _imported(tree, current, is_pkg):
                    bad = _hits(name, prefixes)
                    if bad and (lineno, bad) not in seen:
                        seen.add((lineno, bad))
                        problems.append(f"{os.path.relpath(path, os.path.dirname(app_dir))}:{lineno}: "
                                        f"app.{layer} must not import {bad} ({name})")
    return problems


def main(argv: list[str]) -> int:
    default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
    problems = check(argv[1] if len(argv) > 1 else default)
    for p in problems:
        print(p)
    if problems:
        print(f"layer rule: {len(problems)} violation(s)")
        return 1
    print("layer rule: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
