# repo_scanner/run_scanner.py
import argparse
import json
import traceback
from pathlib import Path

from repo_scanner.scanner import scan_repository
from repo_scanner.ast_engine.graph_builder import build_full_graph
from repo_scanner.analysis_engine.analyzer import analyze_graph
from repo_scanner.workers.inspector import inspect_target


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
                    from repo_scanner.llm_engine.prompt_builder import (
                        build_structured_repo_decision_prompt,
                        build_feedback_reasoning_prompt,
                    )
                    from repo_scanner.llm_engine.reasoning_engine import (
                        LLMConfig,
                        RepoReasoningLLM,
                    )
                    from repo_scanner.llm_engine.output_parser import (
                        parse_repo_decision,
                        repo_decision_to_json,
                        validate_repo_decision_grounding,
                        LLMOutputParseError,
                    )
                    from repo_scanner.llm_engine.feedback_builder import (
                        build_inspection_feedback_context,
                    )
                    from repo_scanner.planner.planner import build_execution_plan

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

                    print("\n===== STRUCTURED DECISION =====")
                    try:
                        decision = parse_repo_decision(raw_response)
                        decision = validate_repo_decision_grounding(decision, scan["files"])
                        print(repo_decision_to_json(decision))

                        # ----- Build execution plan ------------------------------------
                        plan = build_execution_plan(decision, scan=scan)
                        print("\n===== EXECUTION PLAN =====")
                        print(plan.model_dump_json(indent=2))

                        # ----- Inspection step -----------------------------------------
                        repo_root = Path(repo_path)
                        print("\n===== INSPECTION RESULTS =====")
                        inspect_results = []  # collect for feedback loop
                        for step in plan.steps:
                            if getattr(step, "step_type", None) == "inspect":
                                result = inspect_target(
                                    repo_root,
                                    step.target,
                                    step.target_kind,
                                )
                                print(result.model_dump_json(indent=2))
                                # Store a plain dict for the feedback prompt
                                inspect_results.append(result.model_dump())

                        # ----- Feedback loop -------------------------------------------
                        if inspect_results:
                            print("\n===== FEEDBACK LOOP =====")
                            feedback_context = build_inspection_feedback_context(inspect_results)
                            feedback_prompt = build_feedback_reasoning_prompt(
                                original_prompt=prompt,
                                inspect_context=feedback_context,
                            )
                            feedback_response = llm.reason(feedback_prompt)

                            print("\n===== FEEDBACK RAW OUTPUT =====")
                            print(feedback_response)

                            try:
                                refined_decision = parse_repo_decision(feedback_response)
                                refined_decision = validate_repo_decision_grounding(
                                    refined_decision, scan["files"]
                                )
                                print("\n===== REFINED DECISION =====")
                                print(repo_decision_to_json(refined_decision))

                                refined_plan = build_execution_plan(refined_decision, scan=scan)
                                print("\n===== REFINED PLAN =====")
                                print(refined_plan.model_dump_json(indent=2))
                            except Exception as e:
                                # **Fallback** – safe default with empty risks & actions
                                print("[Warning] Feedback parsing failed:", e)
                                fallback = decision.model_copy()
                                fallback.risks = []
                                fallback.recommended_actions = []
                                print("\n===== FALLBACK REFINED DECISION =====")
                                print(repo_decision_to_json(fallback))

                                fallback_plan = build_execution_plan(fallback, scan=scan)
                                print("\n===== FALLBACK REFINED PLAN =====")
                                print(fallback_plan.model_dump_json(indent=2))

                    except LLMOutputParseError as parse_err:
                        # Initial parse failure – attempt repair once (unchanged)
                        print("[Warning] Initial structured parse failed. Attempting repair once...")
                        print(parse_err)

                        from repo_scanner.llm_engine.prompt_builder import build_json_repair_prompt

                        repair_prompt = build_json_repair_prompt(
                            broken_output=raw_response,
                            parse_error=str(parse_err),
                        )
                        repaired_response = llm.reason(repair_prompt)

                        print("\n===== REPAIRED RAW OUTPUT =====")
                        print(repaired_response)

                        try:
                            decision = parse_repo_decision(repaired_response)
                            decision = validate_repo_decision_grounding(decision, scan["files"])
                            print("\n===== REPAIRED STRUCTURED DECISION =====")
                            print(repo_decision_to_json(decision))

                            plan = build_execution_plan(decision, scan=scan)
                            print("\n===== REPAIRED EXECUTION PLAN =====")
                            print(plan.model_dump_json(indent=2))

                            # Inspection after repaired plan (same as above)
                            repo_root = Path(repo_path)
                            print("\n===== INSPECTION RESULTS (REPAIRED) =====")
                            inspect_results = []
                            for step in plan.steps:
                                if getattr(step, "step_type", None) == "inspect":
                                    result = inspect_target(
                                        repo_root,
                                        step.target,
                                        step.target_kind,
                                    )
                                    print(result.model_dump_json(indent=2))
                                    inspect_results.append(result.model_dump())

                            # Feedback loop for repaired output
                            if inspect_results:
                                print("\n===== FEEDBACK LOOP (REPAIRED) =====")
                                feedback_context = build_inspection_feedback_context(inspect_results)
                                feedback_prompt = build_feedback_reasoning_prompt(
                                    original_prompt=repair_prompt,
                                    inspect_context=feedback_context,
                                )
                                feedback_response = llm.reason(feedback_prompt)

                                print("\n===== FEEDBACK RAW OUTPUT (REPAIRED) =====")
                                print(feedback_response)

                                try:
                                    refined_decision = parse_repo_decision(feedback_response)
                                    refined_decision = validate_repo_decision_grounding(
                                        refined_decision, scan["files"]
                                    )
                                    print("\n===== REFINED DECISION (REPAIRED) =====")
                                    print(repo_decision_to_json(refined_decision))

                                    refined_plan = build_execution_plan(refined_decision, scan=scan)
                                    print("\n===== REFINED PLAN (REPAIRED) =====")
                                    print(refined_plan.model_dump_json(indent=2))
                                except Exception as e:
                                    # Fallback for repaired feedback
                                    print("[Warning] Feedback parsing failed (repaired):", e)
                                    fallback = decision.model_copy()
                                    fallback.risks = []
                                    fallback.recommended_actions = []
                                    print("\n===== FALLBACK REFINED DECISION (REPAIRED) =====")
                                    print(repo_decision_to_json(fallback))

                                    fallback_plan = build_execution_plan(fallback, scan=scan)
                                    print("\n===== FALLBACK REFINED PLAN (REPAIRED) =====")
                                    print(fallback_plan.model_dump_json(indent=2))

                        except LLMOutputParseError as repair_err:
                            print("[Warning] Repair failed. Keeping raw output only.")
                            print(repair_err)

                except Exception as llm_err:
                    # Show full traceback so we can see the real failure
                    print("\n[Warning] LLM step failed:")
                    print(type(llm_err).__name__, repr(llm_err))
                    traceback.print_exc()

        except Exception as e:
            print(f"\n[Warning] Failed to build graph or run static analysis: {e}")


if __name__ == "__main__":
    main()
