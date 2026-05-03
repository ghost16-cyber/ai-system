from repo_scanner.scanner import scan_repository
from repo_scanner.language_detector import detect_framework
from repo_scanner.structure_analyzer import analyze_structure

repo_path = "C:/Users/palla/Desktop/ai-system-1"

scan = scan_repository(repo_path)
frameworks = detect_framework(repo_path)
structure = analyze_structure(repo_path)

print("SCAN:", scan)
print("FRAMEWORKS:", frameworks)
print("STRUCTURE:", structure)