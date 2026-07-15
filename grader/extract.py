"""Inject an answer-dump cell into a notebook and read the dumped answers.

Before executing a student notebook we append a final cell that serializes each
question's `var_name` to answers.json in the run dir (/work). After the run we read it.
Everything here is plain nbformat surgery + JSON — no student code runs in THIS
process (it only runs inside the sandbox via runner.py).
"""
from __future__ import annotations

import ast
import json
from typing import Any, Optional

import nbformat

# The dumper runs *inside* the sandbox after the student's cells. It must be
# self-contained (no imports beyond the sandbox stack) and never raise — a
# missing/unserializable variable becomes null, never a crash that voids the run.
_DUMP_TEMPLATE = r"""
# --- pda-grader: answer dump (auto-injected) ---
import json as _pda_json, os as _pda_os

def _pda_ser(v):
    try:
        import numpy as _np
    except Exception:
        _np = None
    try:
        import pandas as _pd
    except Exception:
        _pd = None
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if _np is not None:
        if isinstance(v, _np.generic):
            return v.item()
        if isinstance(v, _np.ndarray):
            return v.tolist()
    if _pd is not None:
        if isinstance(v, _pd.Series):
            return v.tolist()
        if isinstance(v, _pd.DataFrame):
            return {"__df__": True, "columns": [str(c) for c in v.columns],
                    "rows": v.astype(object).where(_pd.notnull(v), None).values.tolist()}
    if isinstance(v, (list, tuple)):
        return [_pda_ser(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _pda_ser(x) for k, x in v.items()}
    try:
        _pda_json.dumps(v)
        return v
    except Exception:
        return None

_pda_vars = __PDA_VARS__
_pda_out = {}
for _pda_k in _pda_vars:
    try:
        _pda_out[_pda_k] = _pda_ser(eval(_pda_k))
    except Exception:
        _pda_out[_pda_k] = None

try:
    with open("answers.json", "w") as _pda_f:   # cwd is /work (writable bind mount)
        _pda_json.dump(_pda_out, _pda_f)
except Exception as _pda_e:
    print("pda-grader dump error:", _pda_e)
# --- end answer dump ---
"""


def build_dump_cell(var_names: list[str]) -> nbformat.NotebookNode:
    uniq = sorted({v for v in var_names if v})
    src = _DUMP_TEMPLATE.replace("__PDA_VARS__", repr(uniq))
    return nbformat.v4.new_code_cell(source=src)


def inject_dump(nb: nbformat.NotebookNode, var_names: list[str]) -> nbformat.NotebookNode:
    """Return a copy of nb with the answer-dump cell appended."""
    nb = nbformat.from_dict(nb)  # shallow copy of structure
    nb.cells = list(nb.cells) + [build_dump_cell(var_names)]
    return nb


def inject_tests(nb: nbformat.NotebookNode, tests_by_marker: dict[str, str]) -> nbformat.NotebookNode:
    """Append a hidden `tests` cell writing per-question pass(1.0)/fail(0.0) to tests.json.

    Each entry is (question_id -> python assert code). The block runs against the
    student's already-executed variables. A question's asserts are all-or-nothing:
    any failing assert => 0.0 for that question, but never crashes the whole run.
    (tests_.py still supports fractional scores if a value between 0 and 1 arrives.)
    """
    nb = nbformat.from_dict(nb)
    header = (
        "# --- pda-grader: hidden tests (auto-injected) ---\n"
        "import json as _pda_tj\n_pda_tests = {}\n"
    )
    body = []
    for qid, code in tests_by_marker.items():
        indented = "\n".join("    " + ln for ln in code.splitlines()) or "    pass"
        body.append(
            f"try:\n{indented}\n"
            f"    _pda_tests[{qid!r}] = 1.0\n"
            f"except Exception:\n"
            f"    _pda_tests[{qid!r}] = 0.0\n"
        )
    footer = (
        "\ntry:\n"
        "    with open('tests.json','w') as _pda_tf:\n"   # cwd is /work
        "        _pda_tj.dump(_pda_tests, _pda_tf)\n"
        "except Exception as _pda_e:\n"
        "    print('pda-grader tests dump error:', _pda_e)\n"
        "# --- end hidden tests ---\n"
    )
    nb.cells = list(nb.cells) + [nbformat.v4.new_code_cell(source=header + "\n".join(body) + footer)]
    return nb


def read_answers(raw: Optional[bytes]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def deserialize_df(val: Any):
    """Turn a dumped {__df__, columns, rows} back into a pandas DataFrame."""
    import pandas as pd
    if isinstance(val, dict) and val.get("__df__"):
        return pd.DataFrame(val.get("rows", []), columns=val.get("columns", []))
    return val


# --- 'auto' authoring: figure out what a graded cell produces ----------------
def cell_source(nb: nbformat.NotebookNode, cell_ref) -> str:
    """The raw source of nb.cells[cell_ref] (cell_ref is a string/int index)."""
    try:
        idx = int(cell_ref)
    except (TypeError, ValueError):
        return ""
    cells = nb.get("cells") or []
    if 0 <= idx < len(cells):
        src = cells[idx].get("source", "")
        return "".join(src) if isinstance(src, list) else str(src)
    return ""


def _names_in_target(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for el in target.elts:
            out += _names_in_target(el)
        return out
    return []


def cell_assign_targets(source: str) -> list[str]:
    """Top-level variables a cell assigns — the answers to grade for an 'auto' cell.

    Parses `x = ...`, `x, y = ...`, `x: T = ...`, `x += ...` at module level only.
    Ignores private (_underscore) names. Best-effort: returns [] on a syntax error.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:  # top-level statements only
        if isinstance(node, ast.Assign):
            for t in node.targets:
                names += _names_in_target(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names += _names_in_target(node.target)
    # de-dupe, preserve order, drop private/dunder
    seen: dict[str, None] = {}
    for n in names:
        if not n.startswith("_"):
            seen.setdefault(n, None)
    return list(seen.keys())
