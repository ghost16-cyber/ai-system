from __future__ import annotations

import shlex
from pathlib import Path

from backend.app.commands.schemas import CommandSuggestion


DANGEROUS_FRAGMENTS = (
    "rm -rf",
    "sudo rm",
    "sudo shutdown",
    "sudo reboot",
    "curl ",
    "| sh",
    "| bash",
    "wget ",
    "cat .env",
    "printenv",
    "chmod -r",
    "chown -r",
)


def suggest_command(
    action: str,
    project_root: str | Path,
    *,
    target: str | None = None,
    working_directory: str | Path | None = None,
) -> CommandSuggestion:
    root = Path(project_root).expanduser().resolve()
    workdir = _resolve_workdir(root, working_directory)
    action_key = action.strip().lower().replace("-", "_")
    if action_key == "pytest":
        suffix = f" {shlex.quote(_safe_relative_target(target))}" if target else ""
        return _suggest(f"python -m pytest -q{suffix}", workdir, "Run the project test suite.", "low", False, "Pytest is a local validation command and is explicit.", "Dots and a final pass/fail summary.")
    npm = {
        "npm_test": ("npm test", "Run the existing npm test script."),
        "npm_run_lint": ("npm run lint", "Run the existing npm lint script."),
        "npm_run_build": ("npm run build", "Run the existing npm build script."),
        "npm_run_typecheck": ("npm run typecheck", "Run the existing npm typecheck script."),
    }
    if action_key in npm:
        command, purpose = npm[action_key]
        return _suggest(command, workdir, purpose, "low", False, "Runs one exact project-declared npm script without a shell.", "Bounded npm script output.")
    if action_key == "node_test":
        suffix = f" {shlex.quote(_safe_relative_target(target))}" if target else ""
        return _suggest(f"node --test{suffix}", workdir, "Run Node's test runner.", "low", False, "Uses Node's structured test runner without shell evaluation.", "Node test totals and failures.")
    if action_key == "python_script":
        script = _safe_relative_target(target or "main.py")
        return _suggest(f"python {shlex.quote(script)}", workdir, f"Run Python script {script}.", "medium", True, "Runs one explicit project script after confirmation.", "Script logs or a Python traceback.")
    if action_key == "streamlit":
        app = _safe_relative_target(target or "dashboard/app.py")
        return _suggest(f"streamlit run {shlex.quote(app)}", workdir, f"Start Streamlit app {app}.", "medium", True, "Starts a local dashboard server after confirmation.", "A localhost URL or Streamlit startup error.")
    if action_key == "docker_ps":
        return _suggest("docker ps", workdir, "List running Docker containers.", "low", False, "Read-only Docker status command.", "A table of running containers.")
    if action_key == "docker_compose_up":
        return _suggest("docker compose up", workdir, "Start services defined in docker-compose.yml.", "medium", True, "Starts project-declared containers after confirmation.", "Container startup logs.")
    if action_key == "list_files":
        return _suggest("python -c \"from pathlib import Path; print('\\n'.join(sorted(p.as_posix() for p in Path('.').iterdir())))\"", workdir, "List top-level project files.", "low", False, "Uses Python pathlib to list only project root entries.", "One path per line.")
    return analyze_command(action, workdir, project_root=root)


def analyze_command(
    command: str,
    working_directory: str | Path,
    *,
    project_root: str | Path | None = None,
) -> CommandSuggestion:
    root = Path(project_root).expanduser().resolve() if project_root is not None else Path(working_directory).expanduser().resolve()
    workdir = _resolve_workdir(root, working_directory)
    normalized = " ".join(command.strip().lower().split())
    if not command.strip():
        return _rejected(command, workdir, "Empty command cannot be analyzed safely.")
    if any(fragment in normalized for fragment in DANGEROUS_FRAGMENTS):
        return _rejected(command, workdir, "Command contains a known high-risk shell fragment.")
    if ";" in command or "&&" in command or "||" in command or "`" in command or "$(" in command:
        return _rejected(command, workdir, "Compound shell fragments are not allowlisted.")
    allowlisted_prefixes = (
        "python -m pytest",
        "python ",
        "streamlit run ",
        "docker ps",
        "docker compose up",
        "ls",
        "dir",
    )
    if normalized.startswith(allowlisted_prefixes):
        risk = "medium" if normalized.startswith(("python ", "streamlit", "docker compose up")) else "low"
        return _suggest(command.strip(), workdir, "User-provided allowlisted command.", risk, risk != "low", "Command matches the deterministic allowlist and stays inside the project root.", "Command-specific output.")
    return _rejected(command, workdir, "Unknown command is not allowlisted.")


def _resolve_workdir(root: Path, working_directory: str | Path | None) -> Path:
    candidate = root if working_directory is None else Path(working_directory).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("Command working directory must stay inside the project root.") from error
    return resolved


def _safe_relative_target(target: str) -> str:
    path = Path(target)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Command target must be a relative path inside the project root.")
    return path.as_posix()


def _suggest(
    command: str,
    workdir: Path,
    purpose: str,
    risk_level: str,
    requires_confirmation: bool,
    why_safe: str,
    expected_output_hint: str,
) -> CommandSuggestion:
    return CommandSuggestion(
        command=command,
        working_directory=str(workdir),
        purpose=purpose,
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        why_safe=why_safe,
        expected_output_hint=expected_output_hint,
        allowed=True,
        executed=False,
    )


def _rejected(command: str, workdir: Path, reason: str) -> CommandSuggestion:
    return CommandSuggestion(
        command=command.strip(),
        working_directory=str(workdir),
        purpose="Rejected command safety review.",
        risk_level="high",
        requires_confirmation=True,
        why_safe=f"Rejected: {reason}",
        expected_output_hint="No command should be executed.",
        allowed=False,
        executed=False,
    )
