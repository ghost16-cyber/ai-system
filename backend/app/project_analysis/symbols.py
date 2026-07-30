from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.app.project_analysis.models import MAX_SYMBOLS_PER_FILE, bounded_text, source_range


def analyze_source(relative_path: str, language: str, text: str, *, allow_parser_helper: bool = True) -> dict[str, Any]:
    if language == "python":
        return _analyze_python(text)
    if language in {"javascript", "typescript", "jsx", "tsx"}:
        if allow_parser_helper:
            parsed = _typescript_parser(text, language)
            if parsed is not None:
                return parsed
        return _analyze_javascript_lexical(text, language)
    if language == "json":
        return _analyze_json(text)
    if language == "yaml":
        return _analyze_yaml(text)
    if language == "markdown":
        return _analyze_markdown(text)
    if language == "config":
        return _analyze_config(text)
    return _empty("partial", "unsupported_structural_language")


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.routes: list[dict[str, Any]] = []
        self._classes: list[str] = []

    def _symbol(self, node: Any, name: str, kind: str, **extra: Any) -> None:
        if len(self.symbols) >= MAX_SYMBOLS_PER_FILE:
            return
        self.symbols.append({"name": name, "kind": kind, "range": source_range(node), **extra})

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [_expr_name(item) for item in node.bases if _expr_name(item)]
        self._symbol(node, node.name, "class", bases=bases, decorators=[_expr_name(item) for item in node.decorator_list if _expr_name(item)])
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, "method" if self._classes else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, "async_method" if self._classes else "async_function")

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        decorators = [_expr_name(item) for item in node.decorator_list if _expr_name(item)]
        parameters = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
        if node.args.vararg:
            parameters.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            parameters.append(f"**{node.args.kwarg.arg}")
        qualified = f"{self._classes[-1]}.{node.name}" if self._classes else node.name
        self._symbol(node, qualified, kind, parameters=parameters, decorators=decorators)
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr.lower() in {"get", "post", "put", "patch", "delete", "route"}:
                route = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str) else None
                self.routes.append({"path": route, "method": decorator.func.attr.upper(), "handler": qualified, "range": source_range(decorator)})
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.imports.append({"module": item.name, "names": [], "level": 0, "range": source_range(node)})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append({"module": node.module or "", "names": [item.name for item in node.names], "level": node.level, "range": source_range(node)})

    def visit_Call(self, node: ast.Call) -> None:
        name = _expr_name(node.func)
        if name:
            self.calls.append({"name": name, "range": source_range(node), "confidence": "high"})
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._symbol(node, target.id, "constant" if target.id.isupper() else "assignment")
        self.generic_visit(node)


def _analyze_python(text: str) -> dict[str, Any]:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return {**_empty("failed", "python_syntax_error"), "syntax_errors": [{"message": bounded_text(error.msg, 180), "line": error.lineno or 0, "offset": error.offset or 0}]}
    visitor = _PythonVisitor()
    visitor.visit(tree)
    tests = [item for item in visitor.symbols if item["name"].split(".")[-1].startswith("test_")]
    return {**_empty("complete"), "symbols": visitor.symbols, "imports": visitor.imports[:160], "calls": visitor.calls[:240], "routes": visitor.routes[:80], "tests": tests[:80]}


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _expr_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    return ""


_TS_HELPER = r'''
const fs=require('fs'); const ts=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(0,'utf8')); const kind={typescript:ts.ScriptKind.TS,tsx:ts.ScriptKind.TSX,jsx:ts.ScriptKind.JSX,javascript:ts.ScriptKind.JS}[input.language];
const f=ts.createSourceFile('project.'+input.language,input.text,ts.ScriptTarget.Latest,true,kind); const symbols=[],imports=[],calls=[],exports=[],routes=[];
function range(n){const a=f.getLineAndCharacterOfPosition(n.getStart(f)),b=f.getLineAndCharacterOfPosition(n.getEnd());return {start_line:a.line+1,end_line:b.line+1}}
function name(n){return n&&n.getText(f).slice(0,160)}
function walk(n){
 if(ts.isImportDeclaration(n)){imports.push({module:n.moduleSpecifier.text,names:n.importClause&&n.importClause.namedBindings?name(n.importClause.namedBindings).replace(/[{}]/g,'').split(',').map(x=>x.trim()).filter(Boolean):[],range:range(n)})}
 if(ts.isFunctionDeclaration(n)&&n.name)symbols.push({name:n.name.text,kind:'function',parameters:n.parameters.map(p=>name(p.name)),range:range(n)});
 if(ts.isClassDeclaration(n)&&n.name)symbols.push({name:n.name.text,kind:'class',range:range(n)});
 if(ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&(n.initializer&&(ts.isArrowFunction(n.initializer)||ts.isFunctionExpression(n.initializer))))symbols.push({name:n.name.text,kind:/^[A-Z]/.test(n.name.text)?'react_component':'function',parameters:n.initializer.parameters.map(p=>name(p.name)),range:range(n)});
 if(ts.isCallExpression(n)){const q=name(n.expression);calls.push({name:q,range:range(n),confidence:'high'});if(/^use[A-Z]/.test(q))symbols.push({name:q,kind:'hook_reference',range:range(n)})}
 if(ts.canHaveModifiers(n)&&ts.getModifiers(n)?.some(m=>m.kind===ts.SyntaxKind.ExportKeyword)){const q=n.name&&name(n.name);if(q)exports.push({name:q,default:ts.getModifiers(n).some(m=>m.kind===ts.SyntaxKind.DefaultKeyword),range:range(n)})}
 ts.forEachChild(n,walk)
} walk(f);
const errors=f.parseDiagnostics.map(d=>({message:ts.flattenDiagnosticMessageText(d.messageText,' '),line:f.getLineAndCharacterOfPosition(d.start||0).line+1})).slice(0,20);
process.stdout.write(JSON.stringify({parse_status:errors.length?'failed':'complete',parser:'typescript_compiler',syntax_errors:errors,symbols:symbols.slice(0,120),imports:imports.slice(0,160),calls:calls.slice(0,240),exports:exports.slice(0,120),routes,tests:symbols.filter(s=>/^(test|it|describe)$/.test(s.name))}));
'''


def _typescript_parser(text: str, language: str) -> dict[str, Any] | None:
    if len(text) > 180_000:
        return None
    node = _node_executable()
    module = Path(__file__).resolve().parents[3] / "frontend" / "node_modules" / "typescript"
    if not node or not module.exists():
        return None
    try:
        result = subprocess.run(
            [node, "-e", _TS_HELPER, str(module)], input=json.dumps({"language": language, "text": text}),
            text=True, capture_output=True, timeout=3, shell=False, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 300_000:
            return None
        payload = json.loads(result.stdout)
        return payload if isinstance(payload, dict) and payload.get("parser") == "typescript_compiler" else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def _node_executable() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    nvm = Path.home() / ".nvm" / "versions" / "node"
    candidates = sorted(nvm.glob("*/bin/node"), reverse=True) if nvm.exists() else []
    return str(candidates[0]) if candidates else None


def _analyze_javascript_lexical(text: str, language: str) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.search(r"\bimport\s+(.*?)\s+from\s+['\"]([^'\"]+)['\"]|\brequire\(['\"]([^'\"]+)['\"]\)", line)
        if match:
            imports.append({"module": match.group(2) or match.group(3), "names": re.findall(r"[A-Za-z_$][\w$]*", match.group(1) or ""), "range": {"start_line": number, "end_line": number}})
        declaration = re.search(r"\b(?:function|class)\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", line)
        if declaration:
            name = declaration.group(1) or declaration.group(2)
            symbols.append({"name": name, "kind": "react_component" if name[:1].isupper() and language in {"jsx", "tsx"} else "function", "range": {"start_line": number, "end_line": number}})
        for call in re.findall(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", line):
            calls.append({"name": call, "range": {"start_line": number, "end_line": number}, "confidence": "low"})
        exported = re.search(r"\bexport\s+(default\s+)?(?:function|class|const|let|var)?\s*([A-Za-z_$][\w$]*)", line)
        if exported:
            exports.append({"name": exported.group(2), "default": bool(exported.group(1)), "range": {"start_line": number, "end_line": number}})
    return {**_empty("partial", "typescript_parser_unavailable"), "parser": "lexical_fallback", "symbols": symbols[:MAX_SYMBOLS_PER_FILE], "imports": imports[:160], "calls": calls[:240], "exports": exports[:120]}


class _DuplicateKey(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateKey(key)
        output[key] = value
    return output


def _analyze_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text, object_pairs_hook=_pairs)
    except (json.JSONDecodeError, _DuplicateKey) as error:
        return {**_empty("failed", "json_parse_error"), "syntax_errors": [{"message": bounded_text(error, 180)}]}
    keys = list(payload)[:120] if isinstance(payload, dict) else []
    scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
    deps = {}
    if isinstance(payload, dict):
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(payload.get(field), dict):
                deps.update({str(key): bounded_text(value, 80) for key, value in list(payload[field].items())[:120]})
    return {**_empty("complete"), "configuration_keys": keys, "scripts": {str(k): bounded_text(v, 160) for k, v in list(scripts.items())[:80]} if isinstance(scripts, dict) else {}, "declared_dependencies": deps}


def _analyze_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml
        payload = yaml.safe_load(text)
    except ImportError:
        keys = [match.group(1) for line in text.splitlines() if (match := re.match(r"^([A-Za-z0-9_.-]+):", line))][:120]
        return {**_empty("partial", "safe_yaml_parser_unavailable"), "configuration_keys": keys}
    except Exception as error:
        return {**_empty("failed", "yaml_parse_error"), "syntax_errors": [{"message": bounded_text(error, 180)}]}
    return {**_empty("complete"), "configuration_keys": list(payload)[:120] if isinstance(payload, dict) else []}


def _analyze_markdown(text: str) -> dict[str, Any]:
    headings = []
    tasks = []
    references = []
    acceptance = []
    for number, line in enumerate(text.splitlines(), 1):
        if match := re.match(r"^(#{1,6})\s+(.+)$", line):
            headings.append({"level": len(match.group(1)), "text": bounded_text(match.group(2), 180), "line": number})
        if match := re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$", line):
            tasks.append({"completed": match.group(1).lower() == "x", "text": bounded_text(match.group(2), 220), "line": number})
        references.extend({"path": value, "line": number} for value in re.findall(r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|js|jsx|ts|tsx|json|ya?ml|md)", line)[:8])
        if re.search(r"\b(?:acceptance|must|should|requirement|expected)\b", line, re.I):
            acceptance.append({"text": bounded_text(line, 240), "line": number})
    return {**_empty("complete"), "headings": headings[:80], "task_items": tasks[:80], "file_references": references[:120], "acceptance_criteria": acceptance[:80]}


def _analyze_config(text: str) -> dict[str, Any]:
    keys = []
    for line in text.splitlines():
        if match := re.match(r"^\s*([A-Za-z0-9_.-]+)\s*(?:=|:)", line):
            keys.append(match.group(1))
    return {**_empty("partial", "bounded_config_keys_only"), "configuration_keys": keys[:120]}


def _empty(status: str, warning: str | None = None) -> dict[str, Any]:
    return {"parse_status": status, "parser": "builtin", "syntax_errors": [], "symbols": [], "imports": [], "exports": [], "calls": [], "routes": [], "tests": [], "configuration_keys": [], "scripts": {}, "declared_dependencies": {}, "warnings": [warning] if warning else []}


__all__ = ["analyze_source"]
