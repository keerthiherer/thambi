from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from .. import safety
from ..config import ROOT
from .registry import register


def _paths(value: list | str | None) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in re.split(r"[,\n]", text) if p.strip()]


def _as_text(value: object, name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return "\n".join(value)
    raise TypeError(f"{name} must be a string or a list of strings")


def _text(path: str) -> tuple[Path, str]:
    p = safety.validate_read(path)
    data = p.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError(f"Binary file: {p}")
    return p, data.decode("utf-8", errors="replace")


def _root(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    return p


def _flag(value: bool | str) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


@register("search_in_file", "Find a literal or regex pattern in one text file with line numbers and context.",
          {"path": "file path", "pattern": "text or regular expression", "regex": "true for regex (default false)",
           "context_lines": "lines before/after each match (default 1)", "max_results": "limit (default 50)"},
          ["path", "pattern"], category="coding")
def search_in_file(path: str, pattern: str, regex: bool | str = False,
                   context_lines: int | str = 1, max_results: int | str = 50) -> str:
    p, text = _text(path)
    is_regex = _flag(regex)
    try:
        rx = re.compile(pattern, re.IGNORECASE) if is_regex else None
    except re.error as e:
        raise ValueError(f"Invalid regular expression: {e}") from e
    lines = text.splitlines()
    ctx = min(5, max(0, int(context_lines)))
    limit = min(200, max(1, int(max_results)))
    hits = 0
    out: list[str] = []
    for idx, line in enumerate(lines):
        matched = bool(rx.search(line)) if rx else pattern.casefold() in line.casefold()
        if not matched:
            continue
        hits += 1
        out.append(f"--- match {hits} at {p}:{idx + 1} ---")
        lo, hi = max(0, idx - ctx), min(len(lines), idx + ctx + 1)
        for n in range(lo, hi):
            out.append(f"{n + 1}: {lines[n]}")
        if hits >= limit:
            out.append(f"... stopped after {limit} matches")
            break
    if not hits:
        return f"No matches for '{pattern}' in {p}."
    return "\n".join(out)


@register("read_many_files", "Read several text files at once with line numbers, within a total character cap.",
          {"paths": "JSON list or comma-separated file paths", "start": "first line per file (default 1)",
           "limit_per_file": "lines per file (default 200)", "max_chars": "total characters (default 20000)"},
          ["paths"], category="coding")
def read_many_files(paths: list | str, start: int | str = 1, limit_per_file: int | str = 200,
                    max_chars: int | str = 20000) -> str:
    files = _paths(paths)[:12]
    if not files:
        raise ValueError("No file paths supplied")
    first = max(1, int(start))
    per_file = min(500, max(1, int(limit_per_file)))
    cap = min(30000, max(1000, int(max_chars)))
    out: list[str] = []
    total = 0
    for item in files:
        p, text = _text(item)
        lines = text.splitlines()
        selected = lines[first - 1:first - 1 + per_file]
        block = [f"=== {p} (lines {first}-{first + len(selected) - 1} of {len(lines)}) ==="]
        block.extend(f"{n}: {line}" for n, line in enumerate(selected, first))
        body = "\n".join(block)
        if total + len(body) > cap:
            out.append(f"... total character cap {cap} reached; remaining files skipped")
            break
        out.append(body)
        total += len(body)
    return "\n\n".join(out)


@register("git_log", "Show recent commit history for a repository (read-only).",
          {"root": "repository folder", "count": "number of commits (default 15)"}, category="coding")
def git_log(root: str = ".", count: int | str = 15) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is not installed")
    n = min(100, max(1, int(count)))
    r = safety.run_fixed([git, "log", "--oneline", "--decorate", "-n", str(n)], cwd=_root(root), timeout=30)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return out or "(no commits)"


@register("python_syntax_check", "Compile Python files in memory to check syntax without executing code.",
          {"paths": "JSON list or comma-separated .py file paths"}, ["paths"], category="coding")
def python_syntax_check(paths: list | str) -> str:
    files = _paths(paths)[:30]
    if not files:
        raise ValueError("No file paths supplied")
    out: list[str] = []
    ok = 0
    failed = 0
    for item in files:
        p = safety.validate_read(item)
        if p.suffix.lower() != ".py":
            out.append(f"SKIP  {p} (not a .py file)")
            continue
        try:
            source = p.read_bytes().decode("utf-8", errors="replace")
            compile(source, str(p), "exec")
            out.append(f"OK    {p}")
            ok += 1
        except SyntaxError as e:
            out.append(f"ERROR {p}:{e.lineno}:{e.offset}: line {e.lineno}: {e.msg}")
            failed += 1
        except OSError as e:
            out.append(f"ERROR {p}: {e}")
            failed += 1
    return f"Checked {len(files)} file(s): {ok} OK, {failed} failed.\n" + "\n".join(out)


@register("diff_text", "Show a unified diff between two text files.",
          {"path_a": "original file", "path_b": "changed file"}, ["path_a", "path_b"], category="coding")
def diff_text(path_a: str, path_b: str) -> str:
    a, text_a = _text(path_a)
    b, text_b = _text(path_b)
    diff = list(difflib.unified_diff(
        text_a.splitlines(), text_b.splitlines(), fromfile=str(a), tofile=str(b), lineterm=""
    ))
    if not diff:
        return f"Files are identical: {a} and {b}"
    return "\n".join(diff[:400])


@register("file_info", "Show file size, timestamps and SHA256 hash.",
          {"path": "file path"}, ["path"], category="files")
def file_info(path: str) -> str:
    p = safety.validate_read(path)
    st = p.stat()
    digest = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(2**20), b""):
            digest.update(chunk)
    import datetime
    return json.dumps({
        "path": str(p),
        "size_bytes": st.st_size,
        "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
    }, indent=2)


@register("patch_file", "Replace exact text in a file, with backup and explicit confirmation.",
          {"path": "file to edit", "old": "exact text to replace", "new": "replacement text",
           "replace_all": "true to replace every occurrence (default false)"},
          ["path", "old", "new"], category="coding", dangerous=True, read_only=False)
def patch_file(path: str, old: str | list[str], new: str | list[str],
               replace_all: bool | str = False) -> str:
    old_text = _as_text(old, "old")
    new_text = _as_text(new, "new")
    if not old_text:
        raise ValueError("'old' must not be empty; include the exact text to replace")
    p = safety.validate_write(path, must_exist=True)
    if p.is_dir():
        raise IsADirectoryError(f"Path is a directory: {p}")
    data = p.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("Refusing to patch a binary file")
    text = data.decode("utf-8")
    count = text.count(old_text)
    all_matches = _flag(replace_all)
    if count == 0:
        raise ValueError(f"Text to replace was not found in {p}. Old text: {old_text[:300]!r}")
    if count > 1 and not all_matches:
        raise ValueError(f"Found {count} matches in {p}. Include more context or set replace_all=true.")
    preview = f"OLD:\n{old_text[:800]}\n\nNEW:\n{new_text[:800]}"
    if not safety.require_confirmation(
        "Patch file?",
        f"{p}\nMatches: {count}\n\n{preview}",
        action="patch_file",
    ):
        raise PermissionError("Patch denied by user")
    backup = p.with_suffix(p.suffix + ".aether.bak")
    shutil.copy2(p, backup)
    updated = text.replace(old_text, new_text) if all_matches else text.replace(old_text, new_text, 1)
    tmp = p.with_suffix(p.suffix + ".aether.tmp")
    tmp.write_bytes(updated.encode("utf-8"))
    os.replace(tmp, p)
    return f"Patched {p}: {count} occurrence(s) replaced. Backup: {backup}"


@register("create_venv", "Create a Python virtual environment using the current interpreter, after confirmation.",
          {"path": "venv folder path (for example .venv)"}, ["path"], category="coding",
          dangerous=True, read_only=False)
def create_venv(path: str) -> str:
    p = safety.validate_path(path)
    if p.exists():
        if not p.is_dir():
            raise FileExistsError(f"Path exists and is not a directory: {p}")
        if any(p.iterdir()):
            raise FileExistsError(f"Virtual environment folder is not empty: {p}")
    if not safety.require_confirmation(
        "Create virtual environment?",
        f"{p}\n\nInterpreter: {sys.executable}\nThis runs: python -m venv <path>",
        action="create_venv",
    ):
        raise PermissionError("Virtual environment creation denied by user")
    result = safety.run_fixed([sys.executable, "-m", "venv", str(p)], cwd=ROOT, timeout=300)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"venv creation failed (exit {result.returncode}): {output[-2000:]}")
    activate = p / ("Scripts/Activate.ps1" if os.name == "nt" else "bin/activate")
    return f"Created virtual environment at {p}\nActivate: {activate}" + (f"\n{output[-1000:]}" if output else "")


@register("venv_pip_install", "Install requirements into a virtual environment using its own pip, after confirmation.",
          {"venv": "virtual environment folder", "requirements": "requirements.txt path"},
          ["venv", "requirements"], category="coding", dangerous=True, read_only=False)
def venv_pip_install(venv: str, requirements: str) -> str:
    env = safety.validate_path(venv, must_exist=True)
    req = safety.validate_read(requirements)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        raise FileNotFoundError(f"No interpreter found in virtual environment: {python}")
    if not safety.require_confirmation(
        "Install requirements?",
        f"Environment: {env}\nRequirements: {req}\nThis runs: python -m pip install -r <requirements>",
        action="venv_pip_install",
    ):
        raise PermissionError("Package installation denied by user")
    result = safety.run_fixed([str(python), "-m", "pip", "install", "-r", str(req)],
                              cwd=ROOT, timeout=900)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"pip install failed (exit {result.returncode}): {output[-3000:]}")
    return f"Installed requirements from {req} into {env}" + (f"\n{output[-1500:]}" if output else "")


def _json_mapping(value: object, name: str) -> dict[str, str]:
    if isinstance(value, dict):
        parsed = value
    else:
        text = str(value or "").strip() or "{}"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} must be a JSON object: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return {str(key).strip(): str(item).strip() for key, item in parsed.items() if str(key).strip()}


def _json_string_list(value: object, name: str) -> list[str]:
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value or "").strip() or "[]"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} must be a JSON list: {e}") from e
    if not isinstance(parsed, list):
        raise ValueError(f"{name} must be a JSON list")
    return [str(item).strip() for item in parsed if str(item).strip()]


def _class_name(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


@register("tool_spec_builder", "Create a reviewed scaffold draft for a new registered tool, with test and documentation files.",
          {"name": "snake_case tool name", "description": "what the tool does",
           "category": "tool category (default general)", "args_json": "JSON object of argument names to descriptions",
           "required_json": "JSON list of required argument names", "dangerous": "true if user confirmation is required",
           "read_only": "true if the tool never writes", "draft_dir": "draft folder (default data/drafts/tool_specs)",
           "overwrite": "true to replace existing draft files (default false)"},
          ["name", "description"], category="coding", dangerous=True, read_only=False)
def tool_spec_builder(name: str, description: str, category: str = "general",
                      args_json: object = "{}", required_json: object = "[]",
                      dangerous: bool | str = False, read_only: bool | str = True,
                      draft_dir: str = "data/drafts/tool_specs",
                      overwrite: bool | str = False) -> str:
    tool_name = str(name or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}", tool_name):
        raise ValueError("Tool name must be 3-40 lowercase snake_case characters")
    summary = " ".join(str(description or "").split())
    if len(summary) < 10:
        raise ValueError("Description must be at least 10 characters")
    args_map = _json_mapping(args_json, "args_json")
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", key) for key in args_map):
        raise ValueError("Argument names must be lowercase snake_case")
    required = _json_string_list(required_json, "required_json")
    unknown_required = [item for item in required if item not in args_map]
    if unknown_required:
        raise ValueError(f"required_json contains undeclared arguments: {', '.join(unknown_required)}")
    signature = ", ".join(f"{key}: str = \"\"" for key in args_map)
    code = f'''from __future__ import annotations

from aether.tools.registry import register


@register(
    {json.dumps(tool_name)},
    {json.dumps(summary)},
    {json.dumps(args_map, indent=4, ensure_ascii=False)},
    {json.dumps(required, ensure_ascii=False)},
    category={json.dumps(str(category or 'general').strip())},
    dangerous={_flag(dangerous)},
    read_only={_flag(read_only)},
)
def {tool_name}({signature}) -> str:
    raise NotImplementedError("Implement '{tool_name}' before registering this draft.")
'''
    test_code = f'''from __future__ import annotations

import unittest


class Test{_class_name(tool_name)}Tool(unittest.TestCase):
    def test_placeholder(self) -> None:
        self.assertTrue(True)
'''
    arg_lines = "\n".join(f"- `{key}`: {value}" for key, value in args_map.items()) or "- none"
    readme = "\n".join([
        f"# {tool_name}",
        "",
        summary,
        "",
        "## Spec",
        f"- Category: {category or 'general'}",
        f"- Dangerous: {_flag(dangerous)}",
        f"- Read-only: {_flag(read_only)}",
        f"- Required: {', '.join(required) if required else 'none'}",
        "",
        "## Arguments",
        arg_lines,
        "",
        "## Next steps",
        "1. Implement the handler in the generated tool file.",
        "2. Add the module import to `aether/tools/registry.py` when it is ready.",
        "3. Replace the placeholder test with real assertions.",
        "4. Run `python -m compileall -q aether tests` and `python -m pytest -q`.",
        "",
    ])
    compiled = {"tool": code, "test": test_code}
    for label, source in compiled.items():
        try:
            compile(source, f"{tool_name}_{label}.py", "exec")
        except SyntaxError as e:
            raise ValueError(f"Generated {label} failed syntax validation: {e}") from e
    base = safety.validate_write(draft_dir)
    if base.exists() and not base.is_dir():
        raise NotADirectoryError(str(base))
    files = {
        base / f"{tool_name}_tool.py": code,
        base / f"test_{tool_name}.py": test_code,
        base / f"{tool_name}_README.md": readme,
    }
    replace = overwrite is True or str(overwrite).strip().lower() in ("1", "true", "yes")
    existing = [str(path) for path in files if path.exists()]
    if existing and not replace:
        raise FileExistsError(f"Draft files already exist: {', '.join(existing)}. Set overwrite=true to replace them.")
    if not safety.require_confirmation(
        "Create tool spec scaffold?",
        f"Folder: {base}\nFiles: {', '.join(str(path.name) for path in files)}\n\n"
        "Drafts are not registered automatically. The handler raises NotImplementedError until implemented.",
        action="tool_spec_builder",
    ):
        raise PermissionError("Tool scaffold creation denied by user")
    base.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return ("Created tool scaffold draft (not registered automatically):\n"
            + "\n".join(f"- {path}" for path in files)
            + "\nSyntax check: OK\nNext: implement the handler, add the registry import, then run tests.")
