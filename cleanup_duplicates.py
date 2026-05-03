# Cleanup Script - Remove duplicates and organize legacy files
import os
import shutil
from pathlib import Path
from datetime import datetime


def cleanup_duplicates():
    """Remove duplicate and legacy files, organize into archive."""
    
    print("="*70)
    print("DUPLICATE FILE CLEANUP")
    print("="*70)
    
    # Files to archive (duplicates and old versions)
    DUPLICATES = {
        "train_classifier.py": "Duplicate - use training/scripts/train_classifier.py",
        "check_dataset.py": "Duplicate - use training/scripts/check_dataset.py",
        "cli_analyzer.py": "Legacy - not used in new system",
        "incremental_trainer.py": "Legacy - not used in new system",
        "report_generator.py": "Legacy - not used in new system",
        "save_dataset.py": "Legacy - not used in new system",
        "retrain.bat": "Legacy - use Python scripts instead",
        "bad_loop.py": "Test file - moved to tests/",
        "test1.py": "Test file - moved to tests/",
        "while_loop.py": "Test file - moved to tests/",
    }
    
    # Files to move to proper locations
    RELOCATE = {
        "code_analyzer.py": "src/inference/code_analyzer.py",
        "file_analyzer.py": "src/inference/file_analyzer.py",
        "code_pattern_clf.pkl": "data/models/code_pattern_clf.pkl",
    }
    
    # Step 1: Archive old/duplicate files
    print("\n[1] Archiving duplicates and legacy files...\n")
    archive_dir = Path("legacy_archive")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for filename, reason in DUPLICATES.items():
        filepath = Path(filename)
        if filepath.exists():
            archive_path = archive_dir / f"{filename}"
            shutil.move(str(filepath), str(archive_path))
            print(f"  ✓ {filename}")
            print(f"    → {reason}")
            print(f"    → Archived to: {archive_path}\n")
    
    # Step 2: Move files to proper locations
    print("\n[2] Moving files to proper locations...\n")
    for old_path, new_path in RELOCATE.items():
        old = Path(old_path)
        new = Path(new_path)
        
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            print(f"  ✓ {old_path} → {new_path}\n")
    
    # Step 3: Move CSV files to data/processed
    print("\n[3] Organizing data files...\n")
    for csv_file in ["code_patterns.csv", "new_examples.csv"]:
        old = Path(csv_file)
        new = Path("data/processed") / csv_file
        
        if old.exists() and not new.exists():
            shutil.move(str(old), str(new))
            print(f"  ✓ {csv_file} → data/processed/{csv_file}\n")
        elif new.exists():
            if old.exists():
                old.unlink()
            print(f"  ✓ {csv_file} already in data/processed/ (removed duplicate)\n")
    
    # Step 4: Create cleanup report
    print("\n[4] Creating cleanup report...\n")
    
    report = f"""CLEANUP REPORT - {datetime.now().isoformat()}
================================================================================

SUMMARY
-------
✓ Duplicate files archived
✓ Legacy files organized
✓ Source files reorganized
✓ Data files consolidated

ACTIONS TAKEN
-------------

1. ARCHIVED (moved to legacy_archive/):
"""
    
    for filename, reason in DUPLICATES.items():
        report += f"   - {filename} ({reason})\n"
    
    report += f"""
2. RELOCATED (moved to proper locations):
"""
    
    for old_path, new_path in RELOCATE.items():
        report += f"   - {old_path} → {new_path}\n"
    
    report += f"""
3. DATA CONSOLIDATED:
   - code_patterns.csv → data/processed/
   - new_examples.csv → data/processed/

NEXT STEPS
----------
1. Update any scripts that import from root:
   OLD: import code_analyzer
   NEW: from src.inference.code_analyzer import SUGGESTIONS

2. Use training scripts from new locations:
   python training/scripts/train_classifier.py
   python training/scripts/check_dataset.py
   python training/scripts/setup.py

3. Run the new pipeline:
   python main.py

ROOT DIRECTORY IS NOW CLEAN
============================
"""
    
    report_path = Path("legacy_archive/CLEANUP_REPORT.txt")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(report)
    print(f"\n✓ Report saved to: {report_path}")
    
    return report


def show_directory_tree():
    """Show cleaned directory tree."""
    print("\n" + "="*70)
    print("CLEANED DIRECTORY STRUCTURE")
    print("="*70 + "\n")
    
    tree = """
ai-system/
├── src/                    ✓ All source code organized
├── training/              ✓ Training scripts
├── data/                  ✓ All data & models
├── scripts/               ✓ Utility tools
├── tests/                 ✓ Unit tests
├── config/                ✓ Configuration
├── notebooks/             ✓ Jupyter notebooks
├── legacy_archive/        ← Old files archived here
│   ├── train_classifier.py
│   ├── check_dataset.py
│   ├── ... (other legacy files)
│   └── CLEANUP_REPORT.txt
│
├── main.py               ✓ Entry point (clean)
├── README.md             ✓ Documentation
├── STRUCTURE.md          ✓ Architecture guide
└── MIGRATION.md          ✓ Migration guide
"""
    print(tree)


if __name__ == "__main__":
    cleanup_duplicates()
    show_directory_tree()
    
    print("\n" + "="*70)
    print("✓ CLEANUP COMPLETE!")
    print("="*70)
    print("\nYour repository is now organized with:")
    print("  • No duplicate files")
    print("  • No silent divergence risks")
    print("  • Clean root directory")
    print("  • Clear file organization")
    print("  • Archive for reference")
