# cli_analyzer.py - Interactive code analysis tool
import joblib
import sys
from code_analyzer import SUGGESTIONS, analyze_code

pipeline = joblib.load("code_pattern_clf.pkl")

def print_banner():
    print("\n" + "=" * 70)
    print("CODE ANALYZER - Interactive Pattern Detection")
    print("=" * 70)
    print("\nCommands:")
    print("  analyze   - Analyze a code snippet")
    print("  file      - Analyze a Python file")
    print("  patterns  - List all 30 patterns")
    print("  help      - Show this menu")
    print("  exit      - Quit")
    print("=" * 70 + "\n")

def analyze_snippet():
    """Interactive code snippet analysis"""
    print("\nPaste code snippet (press Enter twice when done):")
    lines = []
    try:
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                lines.pop()  # Remove last empty line
                break
            lines.append(line)
    except EOFError:
        pass
    
    code = '\n'.join(lines)
    
    if not code.strip():
        print("No code provided.")
        return
    
    result = analyze_code(code)
    
    print("\n" + "-" * 70)
    print("ANALYSIS RESULT")
    print("-" * 70)
    print(f"Pattern: {result['predicted_pattern']}")
    print(f"Issue: {result['issue']}")
    print(f"Suggestion: {result['suggestion']}")
    if result['example']:
        print(f"Example: {result['example']}")
    print("-" * 70 + "\n")

def list_patterns():
    """Show all 30 patterns with descriptions"""
    print("\n" + "=" * 70)
    print("ALL PATTERNS (30 Total)")
    print("=" * 70)
    
    patterns = sorted(SUGGESTIONS.keys())
    
    anti_patterns = [p for p in patterns if not p.startswith('good_') and p not in ['pythonic', 'specific_exception', 'has_docstring', 'named_constant', 'unused_loop_var', 'deep_copy', 'python3_print']]
    good_patterns = [p for p in patterns if p.startswith('good_') or p in ['pythonic', 'specific_exception', 'has_docstring', 'named_constant', 'unused_loop_var', 'deep_copy', 'python3_print']]
    
    print("\nBAD PATTERNS (Anti-patterns):")
    print("-" * 70)
    for i, pattern in enumerate(anti_patterns, 1):
        info = SUGGESTIONS[pattern]
        print(f"{i:2}. {pattern:30} -> {info['suggestion'][:40]}")
    
    print("\nGOOD PATTERNS (To keep/follow):")
    print("-" * 70)
    for i, pattern in enumerate(good_patterns, 1):
        info = SUGGESTIONS[pattern]
        print(f"{i:2}. {pattern:30} -> {info['suggestion'][:40]}")
    
    print("\n" + "=" * 70 + "\n")

def analyze_file_interactive():
    """Interactive file analysis"""
    from file_analyzer import analyze_file
    
    file_path = input("Enter file path: ").strip()
    analyze_file(file_path)

def main():
    print_banner()
    
    if len(sys.argv) > 1:
        # Direct command from args
        cmd = sys.argv[1].lower()
        if cmd == "analyze":
            analyze_snippet()
        elif cmd == "file":
            analyze_file_interactive()
        elif cmd == "patterns":
            list_patterns()
        else:
            print(f"Unknown command: {cmd}")
        return
    
    # Interactive loop
    while True:
        try:
            cmd = input("analyzer> ").strip().lower()
            
            if cmd == "exit" or cmd == "quit":
                print("Goodbye!")
                break
            elif cmd == "analyze":
                analyze_snippet()
            elif cmd == "file":
                analyze_file_interactive()
            elif cmd == "patterns":
                list_patterns()
            elif cmd == "help":
                print_banner()
            elif cmd == "":
                continue
            else:
                print(f"Unknown command: '{cmd}'. Type 'help' for commands.")
        
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
