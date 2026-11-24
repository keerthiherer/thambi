from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

from .. import safety
from ..config import ROOT
from .registry import register


def _root(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    return p


def _git(args: list[str], root: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is not installed")
    r = safety.run_fixed([git, *args], cwd=root, timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    return out.strip() or "(no output)"


@register("git_status", "Show git status for a project.", {"root": "repo folder"}, category="coding")
def git_status(root: str = ".") -> str:
    return _git(["status", "--short", "--branch"], _root(root))


@register("git_diff", "Show git diff (read-only).",
          {"root": "repo folder", "staged": "true for staged diff"}, category="coding")
def git_diff(root: str = ".", staged: bool | str = False) -> str:
    staged = staged is True or str(staged).lower() in ("1", "true", "yes")
    args = ["diff", "--stat", "--patch"] + (["--cached"] if staged else [])
    out = _git(args, _root(root))
    return out[:24000]


def _detect_test_command(root: Path) -> tuple[list[str], str] | None:
    py = sys.executable
    pyproject = root / "pyproject.toml"
    setup_cfg = root / "setup.cfg"
    has_pytest_cfg = False
    for f in (pyproject, setup_cfg, root / "pytest.ini", root / "tox.ini"):
        if f.exists():
            try:
                txt = f.read_text(encoding="utf-8", errors="replace").lower()
                if "pytest" in txt:
                    has_pytest_cfg = True
                    break
            except OSError:
                pass
    if has_pytest_cfg or (root / "tests").is_dir():
        try:
            import pytest  # noqa: F401
            return [py, "-m", "pytest", "-q"], "pytest"
        except ImportError:
            return [py, "-m", "unittest", "discover", "-s", "tests"], "unittest"
    if (root / "package.json").exists():
        npm = shutil.which("npm")
        if npm:
            try:
                pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
                if pkg.get("scripts", {}).get("test"):
                    return [npm, "test", "--"], "npm test"
            except (OSError, ValueError):
                pass
    if (root / "CMakeLists.txt").exists():
        build = root / "build"
        if build.is_dir() and any(build.glob("CTestTestfile.cmake")):
            ctest = shutil.which("ctest")
            if ctest:
                return [ctest, "--test-dir", str(build), "--output-on-failure"], "ctest"
    return None


@register("run_tests", "Detect and run the project's test suite after explicit confirmation.",
          {"root": "project folder", "timeout": "seconds (default 180)"},
          ["root"], category="coding", dangerous=True, read_only=False)
def run_tests(root: str, timeout: int | str = 180) -> str:
    r = _root(root)
    det = _detect_test_command(r)
    if not det:
        return "No supported test runner detected (pytest, npm test, or an existing CTest build). Nothing was run."
    cmd, label = det
    if not safety.require_confirmation(
        "Run project tests?",
        f"{label}\nCommand: {' '.join(cmd)}\nFolder: {r}\n\nTests execute project code.",
        action="run_tests",
    ):
        raise PermissionError("Test run denied by user")
    proc = safety.run_fixed(cmd, cwd=r, timeout=min(900, max(10, int(timeout))))
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"$ {label}\nexit={proc.returncode}\n\n{out[-16000:]}"


@register("run_script", "Run a validated local Python/PowerShell/batch script after confirmation.",
          {"path": "script path", "args": "list of arguments", "timeout": "seconds (default 120)"},
          ["path"], category="coding", dangerous=True, read_only=False)
def run_script(path: str, args: list | str | None = None, timeout: int | str = 120) -> str:
    p = safety.validate_read(path)
    ext = p.suffix.lower()
    if ext not in (".py", ".ps1", ".bat", ".cmd"):
        raise PermissionError("Only .py, .ps1, .bat and .cmd scripts may be run")
    if not safety.within(p, safety.allowed_roots()) and not safety.within(p, [Path.home()]):
        raise PermissionError("Script is outside allowed roots")
    if args is None:
        argv: list[str] = []
    elif isinstance(args, list):
        argv = [str(a) for a in args]
    else:
        argv = str(args).split()
    if any(re.search(r"[&|<>^%`]", a) for a in argv):
        raise PermissionError("Shell metacharacters are not allowed in script arguments")
    if ext == ".py":
        cmd = [sys.executable, str(p), *argv]
    elif ext == ".ps1":
        cmd = ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(p), *argv]
    else:
        cmd = ["cmd.exe", "/d", "/c", str(p), *argv]
    if not safety.require_confirmation(
        "Run script?",
        f"{p}\nArguments: {argv}\n\nScripts can modify files and run code.",
        action="run_script",
    ):
        raise PermissionError("Script run denied by user")
    proc = safety.run_fixed(cmd, cwd=p.parent, timeout=min(900, max(5, int(timeout))))
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"$ {' '.join(cmd)}\nexit={proc.returncode}\n\n{out[-16000:]}"


_CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".ps1", ".bat", ".cmd"}
_GENERIC_PATTERNS = [
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "HIGH", "private_key", "Move the key out of source and load it from a secret store."),
    (r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*[\"'][^\"']{6,}[\"']", "HIGH", "hardcoded_secret", "Replace the literal with environment configuration or a secret manager."),
    (r"(?i)AKIA[0-9A-Z]{16}", "HIGH", "aws_access_key", "Revoke and rotate the exposed AWS key."),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "HIGH", "github_token", "Revoke and rotate the exposed GitHub token."),
    (r"sk-[A-Za-z0-9]{20,}", "HIGH", "api_token", "Revoke and rotate the exposed API token."),
    (r"\beval\s*\(", "HIGH", "dynamic_eval", "Avoid eval; parse known input explicitly."),
    (r"\bexec\s*\(", "HIGH", "dynamic_exec", "Avoid exec; use a fixed registered operation."),
    (r"\bos\.system\s*\(", "HIGH", "shell_injection", "Use an argv list with no shell interpretation."),
    (r"shell\s*=\s*True", "HIGH", "shell_true", "Run with shell=False and pass arguments as a list."),
    (r"verify\s*=\s*False", "HIGH", "tls_verification_disabled", "Keep TLS verification enabled."),
    (r"pickle\.loads?\s*\(", "MEDIUM", "unsafe_deserialization", "Use JSON or a trusted deserializer for untrusted data."),
    (r"yaml\.load\s*\(", "MEDIUM", "unsafe_yaml", "Use yaml.safe_load for untrusted YAML."),
    (r"(?i)chmod\s+777", "MEDIUM", "world_writable_permissions", "Use the narrowest permissions that work."),
]
_SECRET_NAMES = ("password", "passwd", "secret", "api_key", "apikey", "access_token", "token", "private_key")
_SECRET_ASSIGN_RX = re.compile(r"(?i)^(?:self\.)?(" + "|".join(_SECRET_NAMES) + r")$")


def _as_paths(value: list | str | None) -> list[Path]:
    if isinstance(value, list):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value or "").strip()
        if not text:
            raw = []
        elif text.startswith("["):
            try:
                parsed = json.loads(text)
                raw = [str(item).strip() for item in parsed if str(item).strip()] if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                raw = [text]
        else:
            raw = [item.strip() for item in re.split(r"[,\n]", text) if item.strip()]
    paths: list[Path] = []
    for item in raw[:60]:
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = ROOT / p
        p = p.resolve()
        if p.is_dir():
            paths.extend(sorted(q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in _CODE_SUFFIXES)[:60])
        elif p.is_file():
            paths.append(p)
        else:
            raise FileNotFoundError(str(p))
    return paths[:60]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}"
    return ""


def _python_findings(path: Path, text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [{"severity": "HIGH", "line": str(e.lineno or 1), "rule": "syntax_error",
                 "message": e.msg, "fix": "Fix the syntax error before relying on this file."}]
    for node in ast.walk(tree):
        line = int(getattr(node, "lineno", 1) or 1)
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append({"severity": "MEDIUM", "line": str(line), "rule": "bare_except",
                             "message": "Bare except catches system exceptions and hides bugs.",
                             "fix": "Catch the specific exception type you can handle."})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
            if any(isinstance(default, (ast.List, ast.Dict, ast.Set)) for default in defaults):
                findings.append({"severity": "MEDIUM", "line": str(line), "rule": "mutable_default",
                                 "message": f"Function '{node.name}' uses a mutable default argument.",
                                 "fix": "Use None as the default and create the container inside the function."})
            end_line = int(getattr(node, "end_lineno", line) or line)
            if end_line - line > 80:
                findings.append({"severity": "LOW", "line": str(line), "rule": "long_function",
                                 "message": f"Function '{node.name}' spans {end_line - line} lines.",
                                 "fix": "Split the function into smaller named steps."})
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in ("eval", "exec"):
                findings.append({"severity": "HIGH", "line": str(line), "rule": "dynamic_code",
                                 "message": f"Call to {name} executes dynamic code.",
                                 "fix": "Replace it with explicit parsing and fixed operations."})
            if name.startswith("pickle.load") or name == "yaml.load":
                findings.append({"severity": "MEDIUM", "line": str(line), "rule": "unsafe_deserialization",
                                 "message": f"Call to {name} can execute data-supplied code.",
                                 "fix": "Use JSON, pickle only for trusted data, or yaml.safe_load."})
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None or not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                target_name = ""
                if isinstance(target, ast.Name):
                    target_name = target.id
                elif isinstance(target, ast.Attribute):
                    target_name = target.attr
                if _SECRET_ASSIGN_RX.match(target_name):
                    findings.append({"severity": "HIGH", "line": str(line), "rule": "hardcoded_secret",
                                     "message": f"Secret-like name '{target_name}' is assigned a string literal.",
                                     "fix": "Load the value from configuration or a secret manager."})
    return findings


def _scan_code(paths: list[Path], mode: str, max_findings: int) -> str:
    findings: list[dict[str, str]] = []
    for path in paths:
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            continue
        text = data.decode("utf-8", errors="replace")
        if path.suffix.lower() == ".py":
            findings.extend(_python_findings(path, text))
        for index, line in enumerate(text.splitlines(), 1):
            for pattern, severity, rule, fix in _GENERIC_PATTERNS:
                if re.search(pattern, line):
                    findings.append({"severity": severity, "line": str(index), "rule": rule,
                                     "message": f"Detected {rule.replace('_', ' ')}.", "fix": fix})
    if mode == "security":
        findings = [item for item in findings if item["rule"] not in ("long_function", "bare_except", "mutable_default")]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda item: (order.get(item["severity"], 3), item["rule"], int(item["line"])))
    counts = {level: sum(1 for f in findings if f["severity"] == level) for level in order}
    title = "CODE REVIEW" if mode == "review" else "SECURITY SCAN"
    lines = [
        title,
        f"Files scanned: {len(paths)}",
        f"Findings: {len(findings)} (HIGH {counts['HIGH']}, MEDIUM {counts['MEDIUM']}, LOW {counts['LOW']})",
        "",
    ]
    for item in findings[:max_findings]:
        lines.append(f"- {item['severity']} {item['rule']} at {item['line']}: {item['message']}")
        lines.append(f"  Fix: {item['fix']}")
    if len(findings) > max_findings:
        lines.append(f"... {len(findings) - max_findings} more finding(s) omitted.")
    if not findings:
        lines.append("No deterministic issues detected. This is not a proof of correctness.")
    return "\n".join(lines)


@register("code_review", "Run deterministic code review checks for correctness risks and maintainability issues.",
          {"paths": "JSON list, comma-separated files, or folder", "max_findings": "limit (default 30, max 60)"},
          ["paths"], category="coding")
def code_review(paths: list | str, max_findings: int | str = 30) -> str:
    try:
        limit = min(60, max(1, int(max_findings)))
    except (TypeError, ValueError):
        limit = 30
    chosen = _as_paths(paths)
    if not chosen:
        return "No supported source files found."
    return _scan_code(chosen, "review", limit)


@register("security_scan", "Scan source files for deterministic security risks such as secrets, shell use, and unsafe deserialization.",
          {"paths": "JSON list, comma-separated files, or folder", "max_findings": "limit (default 30, max 60)"},
          ["paths"], category="coding")
def security_scan(paths: list | str, max_findings: int | str = 30) -> str:
    try:
        limit = min(60, max(1, int(max_findings)))
    except (TypeError, ValueError):
        limit = 30
    chosen = _as_paths(paths)
    if not chosen:
        return "No supported source files found."
    return _scan_code(chosen, "security", limit)


@register("test_generator", "Generate pytest stub tests for public functions and classes in a Python file.",
          {"path": "Python source file", "test_dir": "test folder (default tests)",
           "overwrite": "true to replace an existing generated test file (default false)"},
          ["path"], category="coding", dangerous=True, read_only=False)
def test_generator(path: str, test_dir: str = "tests", overwrite: bool | str = False) -> str:
    p = safety.validate_read(path)
    if p.suffix.lower() != ".py":
        raise ValueError("test_generator only supports Python files")
    source = p.read_bytes().decode("utf-8", errors="replace")
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            if node.name not in names:
                names.append(node.name)
    if not names:
        return f"No public functions or classes found in {p}."
    module = p.stem
    try:
        relative = p.resolve().relative_to(ROOT)
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts:
            module = ".".join(parts)
    except ValueError:
        pass
    valid_module = bool(re.match(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$", module))
    output = [
        "from __future__ import annotations",
        "",
        "import pytest",
    ]
    if valid_module:
        imports = ", ".join(names[:15])
        if len(imports) + len("from  import ") > 100:
            output.append(f"from {module} import (")
            output.extend(f"    {name}," for name in names[:15])
            output.append(")")
        else:
            output.append(f"from {module} import {imports}")
    output.extend(["", ""])
    for name in names[:15]:
        safe = re.sub(r"\W+", "_", name).lower()
        output.extend([
            f"def test_{safe}_stub() -> None:",
            f"    pytest.skip(\"Add arguments and expected result for {name}\")",
            "",
            "",
        ])
    target_dir = safety.validate_write(test_dir)
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
    if not target_dir.is_dir():
        raise NotADirectoryError(str(target_dir))
    destination = target_dir / f"test_{p.stem}.py"
    if destination.exists() and not (overwrite is True or str(overwrite).strip().lower() in ("1", "true", "yes")):
        raise FileExistsError(f"Test file already exists: {destination}. Set overwrite=true to replace it.")
    body = "\n".join(output).rstrip() + "\n"
    if not safety.require_confirmation(
        "Generate test stubs?",
        f"{destination}\nModule: {module if valid_module else 'manual import required'}\n"
        f"Functions/classes: {', '.join(names[:15])}\n\nA local test stub file will be written.",
        action="test_generator",
    ):
        raise PermissionError("Test generation denied by user")
    destination.write_text(body, encoding="utf-8")
    return f"Generated pytest stubs: {destination}\nItems: {len(names[:15])}\nModule import: {module if valid_module else 'manual'}"
