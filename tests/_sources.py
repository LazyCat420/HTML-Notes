"""Where the server's source actually lives, for the guards that read it as TEXT.

`app/main.py` used to be the whole server, so every source-scanning guard did
`pathlib.Path(main.__file__).read_text()`. The request path has since been split
across `app/routes/`, `app/services/` and `app/llm.py`, and each of those starts
with `sys.modules[__name__].__dict__.update(main.__dict__)` — so every SYMBOL is
still reachable as `main.<name>`, but the TEXT that defines it is not in
main.py any more.

That broke ~30 guards at once, and it broke them in the worst way: a guard whose
`src.index(...)` raises `ValueError: substring not found` is loud, but a guard
that merely asserts `"some string" in MAIN_SRC` and passes because the string is
still incidentally there protects nothing. Reading the wrong file is not a test
failure, it is a test that has quietly stopped being about anything.

Import the specific constant for the file that owns the behaviour under test, or
`SERVER_SRC` when the question is genuinely "does this appear anywhere in the
request path". Prefer the specific one: it is the difference between "the video
override runs before the classifier" and "these two strings both exist somewhere".
"""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Ordered roughly the way a turn flows through them.
_FILES = {
    "MAIN_SRC": APP / "main.py",
    "MESSAGE_SRC": APP / "routes" / "message.py",
    "INTERNAL_SRC": APP / "routes" / "internal.py",
    "LLM_SRC": APP / "llm.py",
    "SEARCH_SRC": APP / "services" / "search.py",
    "BUILDERS_SRC": APP / "config_builders.py",
    "CANVAS_SRC": APP / "canvas_manager.py",
    "UTILS_SRC": APP / "utils.py",
    "FINANCE_SRC": APP / "services" / "finance.py",
}

_missing = [str(p) for p in _FILES.values() if not p.exists()]
if _missing:                       # a rename must fail here, not silently pass
    raise RuntimeError(
        "tests/_sources.py points at files that no longer exist: "
        + ", ".join(_missing)
        + " — repoint it rather than letting the guards read an empty room.")

MAIN_SRC = _FILES["MAIN_SRC"].read_text()
MESSAGE_SRC = _FILES["MESSAGE_SRC"].read_text()
INTERNAL_SRC = _FILES["INTERNAL_SRC"].read_text()
LLM_SRC = _FILES["LLM_SRC"].read_text()
SEARCH_SRC = _FILES["SEARCH_SRC"].read_text()
BUILDERS_SRC = _FILES["BUILDERS_SRC"].read_text()
CANVAS_SRC = _FILES["CANVAS_SRC"].read_text()
UTILS_SRC = _FILES["UTILS_SRC"].read_text()
FINANCE_SRC = _FILES["FINANCE_SRC"].read_text()

#: Every module the request path spans, concatenated. For "does this string
#: exist ANYWHERE on the server" questions only — never for ordering, because
#: offsets across a concatenation are meaningless.
SERVER_SRC = "\n".join(p.read_text() for p in _FILES.values())


def trees():
    """(name, ast.Module) for every server file, for AST-walking guards."""
    return [(name, ast.parse(path.read_text())) for name, path in _FILES.items()]


def tree(name: str) -> ast.Module:
    """The AST of one server file, keyed by the constant name (e.g. 'MESSAGE_SRC')."""
    return ast.parse(_FILES[name].read_text())
