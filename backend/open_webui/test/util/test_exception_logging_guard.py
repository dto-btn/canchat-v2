"""OBS-18 guard: log and print calls never render a caught exception as text.

The redaction filter can replace an exception *object* passed to a log call
with its type, but text built before the call (``f"{e}"``, ``str(e)``) reaches
the sinks unchanged, and ``print`` bypasses logging altogether. Log the
exception type instead: ``log.error("... failed: %s", type(e).__name__)``.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
ROOTS = (BACKEND / "open_webui", BACKEND / "mcp_backend")
LOG_METHODS = {
    "exception",
    "error",
    "warning",
    "warn",
    "info",
    "debug",
    "critical",
    "fatal",
    "log",
}
SKIP_DIRS = {"test", "tests", ".venv", "venv", "node_modules", "__pycache__"}


def _call_kind(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id == "print":
        return "print"
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        return "log"
    return None


def _renders(node: ast.AST, name: str) -> bool:
    """``e`` inside an f-string, or ``str(e)``/``repr(e)``/``format(e)``."""
    if isinstance(node, ast.FormattedValue):
        value = node.value
        return (isinstance(value, ast.Name) and value.id == name) or _renders(
            value, name
        )
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"str", "repr", "format"}
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == name
    )


def _violations(path: Path, base: Path = BACKEND) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found = []
    for handler in ast.walk(tree):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        name = handler.name
        for call in ast.walk(handler):
            if not isinstance(call, ast.Call):
                continue
            kind = _call_kind(call)
            if kind is None:
                continue
            leaks = any(_renders(node, name) for node in ast.walk(call))
            if kind == "print":
                leaks = leaks or any(
                    isinstance(arg, ast.Name) and arg.id == name for arg in call.args
                )
            if leaks:
                relative = path.relative_to(base)
                found.append(f"{relative}:{call.lineno}: {ast.unparse(call)[:120]}")
    return found


def _sources():
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if SKIP_DIRS.isdisjoint(path.relative_to(BACKEND).parts):
                yield path


def test_scanner_detects_exception_text(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        """
try:
    pass
except Exception as e:
    log.error(f"failed: {e}")
    log.warning("failed: " + str(e))
    print(e)
    log.error("failed: %s", type(e).__name__)
    log.exception(e)
""",
        encoding="utf-8",
    )
    lines = [v.split(":")[1] for v in _violations(sample, tmp_path)]
    assert lines == ["5", "6", "7"]


def test_no_exception_text_in_log_or_print_calls():
    violations = [v for path in _sources() for v in _violations(path)]
    assert (
        not violations
    ), "Log the exception type, not its text (OBS-18):\n" + "\n".join(violations)
