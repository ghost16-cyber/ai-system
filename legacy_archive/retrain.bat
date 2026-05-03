@echo off
REM Color codes for output
cls
echo.
echo ========================================================================
echo                    MODEL RETRAINING WORKFLOW
echo ========================================================================
echo.

REM Check if new_examples.csv exists
if not exist new_examples.csv (
    echo ERROR: new_examples.csv not found.
    echo Run: python file_analyzer.py ^<file^.py^> --validate
    exit /b 1
)

echo Step 1: Checking new examples...
for /f %%A in ('find /c /v "" ^< new_examples.csv') do set lines=%%A
echo   Found %lines% corrected examples

echo.
echo Step 2: Merging with main dataset...
type new_examples.csv >> code_patterns.csv
echo   ✓ Appended to code_patterns.csv

echo.
echo Step 3: Retraining model...
python train_classifier.py

echo.
echo Step 4: Cleaning up...
del new_examples.csv
echo   ✓ Deleted new_examples.csv

echo.
echo ========================================================================
echo                       SUCCESS!
echo   Model updated and ready for next scan
echo ========================================================================
echo.
pause