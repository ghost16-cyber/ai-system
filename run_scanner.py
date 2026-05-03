import argparse
import json
from pathlib import Path

from repo_scanner.scanner import scan_repository


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


def main():
    parser = argparse.ArgumentParser(description="Scan a repository and extract structure metadata.")
    parser.add_argument("path", nargs="?", default=".", help="Repository path to scan.")
    parser.add_argument("--json", action="store_true", help="Print full JSON scan data.")
    parser.add_argument("--no-ast", action="store_true", help="Skip Python AST parsing.")
    parser.add_argument("--limit", type=int, default=20, help="Number of files to show in summary mode.")
    args = parser.parse_args()

    repo_path = Path(args.path)
    scan = scan_repository(repo_path, include_ast=not args.no_ast)

    if args.json:
        print(json.dumps(scan, indent=2))
    else:
        print_summary(scan, args.limit)


if __name__ == "__main__":
    main()
