# repo_scanner/run_scanner.py
import argparse
import json
import traceback
from pathlib import Path

from .scanner import scan_repository
from .ast_engine.graph_builder import build_full_graph
from .analysis_engine.analyzer import analyze_graph
from .workers.inspector import inspect_target
from .execution.executor import execute_plan
from .intelligence.critic import evaluate_inspection


def print_summary(scan, limit):
    summary = scan["summary"]
    print(f"Repository: {scan['root']}")
    print(f"Files: {summary['total_files']}")
    print(f"Directories: {summary['total_directories']}")
    print(f"Size: {summary['total_size_bytes']} bytes")
    print(f"Languages: {scan['languages']}")
    print(f"File types: {scan['file_types']}")
    print(f"Frameworks: {scan['frameworks']}")
    print(f"Structure: {scan['structure']}")

    python_data = scan["python"]
    print(f"Python files parsed: {python_data['files_parsed']}")
    print(f"Python parse errors: {len(python_data['parse_errors'])}")

    if limit:
        print(f"\nFirst {limit} files:")
        for file_info in scan["files"][:limit]:
            print(f"- {file_info['path']} ({file_info['language']}, {file_info['size_bytes']} bytes)")


def print_graph_samples(graph):
    """Tiny snapshot of the generated graphs."""
    print("\n===== SAMPLE FILE ANALYSIS =====")
    for file, data in list(graph.get("files_data", {}).items())[:1]:
        print("FILE:", file)
        print("Functions:", data.get("functions", [])[:10])
        print("Classes:", data.get("classes", [])[:10])
        print("Imports:", data.get("imports", [])[:10])
        print("Calls:", data.get("calls", [])[:10])
        break

    print("\n===== DEPENDENCY GRAPH SAMPLE =====")
    for k, v in list(graph.get("dependency_graph", {}).items())[:5]:
        print(f"{k} -> {v[:5]}")

    print("\n===== CALL GRAPH SAMPLE =====")
    for k, v in list(graph.get("call_graph", {}).items())[:10]:
        print(f"{k} -> {v[:5]}")


def main():
    parser = argparse.ArgumentParser(
        description="Scan a repository and extract structure metadata."
    )
    parser.add_argument("path", nargs="?", default=".", help="Repository path to scan.")
    parser.add_argument("--json", action="store_true", help="Print full JSON scan data.")
    parser.add_argument("--no-ast", action="store_true", help="Skip Python AST parsing.")
    parser.add_argument(
        "--limit", type=int, default=20, help="Number of files to show in summary mode."
    )
    parser.add_argument(
        "--llm", action="store_true", help="Run LLM reasoning after static analysis."
    )
    parser.add_argument(
        "--llm-max-new-tokens",
        type=int,
        default=500,
        help="Maximum new tokens for structured LLM reasoning.",
    )
    args = parser.parse_args()

    repo_path = Path(args.path)
    scan = scan_repository(repo_path, include_ast=not args.no_ast)

    if args.json:
        print(json.dumps(scan, indent=2))
    else:
        print_summary(scan, args.limit)

    # -----------------------------------------------------------------
    # Build graph, run static analysis, and optionally invoke the LLM
    # -----------------------------------------------------------------
    if not args.no_ast:
        try:
            # 1️⃣ Build full graph
            graph = build_full_graph(repo_path)
            print_graph_samples(graph)

            # 2️⃣ Run static analysis
            analysis = analyze_graph(graph)

            # 3️⃣ Optional LLM reasoning
            if args.llm:
                try:
                    # Lazy imports – only when the flag is used
                    from .llm_engine.prompt_builder import (
                        build_structured_repo_decision_prompt,
                        build_feedback_reasoning_prompt,
                    )
                    from .llm_engine.reasoning_engine import (
                        LLMConfig,
                        RepoReasoningLLM,
                    )
                    from .llm_engine.output_parser import (
                        parse_repo_decision,
                        repo_decision_to_json,
                        validate_repo_decision_grounding,
                        LLMOutputParseError,
                    )
                    from .llm_engine.feedback_builder import (
                        build_inspection_feedback_context,
                    )
                    from .planner.planner import build_execution_plan

                    print("\n===== LLM REASONING =====")
                    # Build a compact summary for the prompt builder
                    scan_summary = {
                        "repository": str(repo_path),
                        "files": scan["files"],                     # trimmed inside builder
                        "total_files": scan["summary"]["total_files"],
                        "total_directories": scan["summary"]["total_directories"],
                        "size_bytes": scan["summary"]["total_size_bytes"],
                        "languages": scan["languages"],
                        "frameworks": scan["frameworks"],
                        "structure": scan["structure"],
                        "python_files_parsed": scan["python"]["files_parsed"],
                        "python_parse_errors": scan["python"]["parse_errors"],
                    }

                    # ---- Structured prompt -------------------------------------------------
                    prompt = build_structured_repo_decision_prompt(
                        scan_summary=scan_summary,
                        graph_analysis=analysis,
                        user_task="Analyze this repository and recommend what to inspect or build next.",
                    )

                    llm = RepoReasoningLLM(
                        LLMConfig(
                            max_new_tokens=args.llm_max_new_tokens,
                            temperature=0.0,
                        )
                    )
                    raw_response = llm.reason(prompt)

                    print("\n===== RAW LLM OUTPUT =====")
                    print(raw_response)

                    # -----------------------------------------------------------------
                    # Parse the initial decision (repair if needed)
                    # -----------------------------------------------------------------
                    try:
                        decision = parse_repo_decision(raw_response)
                        decision = validate_repo_decision_grounding(decision, scan["files"])
                    except LLMOutputParseError as parse_err:
                        print("[Warning] Initial parse failed. Attempting repair once...")
                        print(parse_err)

                        from .llm_engine.prompt_builder import build_json_repair_prompt

                        repair_prompt = build_json_repair_prompt(
                            broken_output=raw_response,
                            parse_error=str(parse_err),
                        )
                        repaired_response = llm.reason(repair_prompt)

                        print("\n===== REPAIRED RAW OUTPUT =====")
                        print(repaired_response)

                        decision = parse_repo_decision(repaired_response)
                        decision = validate_repo_decision_grounding(decision, scan["files"])

                    # -----------------------------------------------------------------
                    # Iterative feedback loop (max 3 iterations)
                    # -----------------------------------------------------------------
                    MAX_ITER = 3
                    current_decision = decision
                    repo_root = Path(repo_path)
                    history = []  # for reward-based adjustments in PriorityEngine

                    for i in range(MAX_ITER):
                        print(f"\n===== ITERATION {i + 1} =====")
                        # Build plan with priority re‑ranking
                        plan = build_execution_plan(
                            current_decision,
                            scan=scan,
                            graph_analysis={**analysis, "history": history},
                        )
                        print("\n--- EXECUTION PLAN ---")
                        print(plan.model_dump_json(indent=2))

                        # Execute up to 3 inspection steps
                        inspect_results = execute_plan(
                            plan,
                            repo_root,
                            inspect_target,
                            max_steps=3,
                        )
                        if not inspect_results:
                            print("[STOP] No actionable inspection results.")
                            break

                        scored_results = []
                        for result in inspect_results:
                            reward = evaluate_inspection(result)
                            result["reward"] = reward  # attach reward for feedback loop
                            scored_results.append(result)
                            
                        print("\n--- INSPECTION RESULTS WITH REWARD ---")
                        for r in scored_results:
                            print(f"[REWARD] {r.get('reward', 0.0):+.2f} | Target: {r.get('target')} | Summary: {r.get('file_summary', {}).get('summary', 'N/A')[:100]}")

                        history.extend(scored_results)  # accumulate history for better feedback



                        # Early stop on high confidence
                        if current_decision.confidence > 0.9:
                            print("[STOP] High confidence reached; stopping iterations.")
                            break

                        # Build feedback context & ask LLM to refine
                        feedback_context = build_inspection_feedback_context(inspect_results)
                        feedback_prompt = build_feedback_reasoning_prompt(
                            original_prompt=prompt,
                            inspect_context=feedback_context,
                        )
                        feedback_response = llm.reason(feedback_prompt)

                        print("\n===== FEEDBACK RAW OUTPUT =====")
                        print(feedback_response)

                        try:
                            refined = parse_repo_decision(feedback_response)
                            refined = validate_repo_decision_grounding(
                                refined, scan["files"]
                            )
                            current_decision = refined
                            print("\n--- REFINED DECISION ---")
                            print(repo_decision_to_json(current_decision))
                        except Exception as e:
                            # **Fallback** – safe default with empty risks & actions
                            print("[Warning] Feedback parsing failed:", e)
                            fallback = current_decision.model_copy()
                            fallback.risks = []
                            fallback.recommended_actions = []
                            print("\n--- FALLBACK REFINED DECISION ---")
                            print(repo_decision_to_json(fallback))
                            current_decision = fallback
                            break  # stop further iterations

                    # Final plan after loop
                    final_plan = build_execution_plan(
                        current_decision,
                        scan=scan,
                        graph_analysis={**analysis, "history": history},
                    )
                    print("\n===== FINAL EXECUTION PLAN =====")
                    print(final_plan.model_dump_json(indent=2))

                except Exception as llm_err:
                    # Show full traceback so we can see the real failure
                    print("\n[Warning] LLM step failed:")
                    print(type(llm_err).__name__, repr(llm_err))
                    traceback.print_exc()

        except Exception as e:
            print(f"\n[Warning] Failed to build graph or run static analysis: {e}")


if __name__ == "__main__":
    main()
