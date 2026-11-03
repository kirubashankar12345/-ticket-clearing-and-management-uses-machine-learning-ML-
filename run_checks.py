"""
run_checks.py - Verify installation, run init_db, and run tests.
Execute: python run_checks.py
"""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

def run(cmd, description):
    print(f"\n{'='*60}")
    print(f"STEP: {description}")
    print(f"CMD:  {' '.join(cmd)}")
    print('='*60)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    print(f"Return code: {result.returncode}")
    return result.returncode == 0

py = sys.executable

# Step 1: Install requirements
ok = run([py, "-m", "pip", "install", "-r", "requirements.txt"], "Install requirements")

# Step 2: Init DB
ok2 = run([py, "init_db.py"], "Initialize DB and seed data")

# Step 3: App startup check
ok3 = run([py, "-c", "from app import create_app; app = create_app(); print('App starts OK')"], "App startup check")

# Step 4: Run tests
ok4 = run([py, "-m", "pytest", "tests/", "-v", "--tb=short", "-x"], "Run test suite")

print("\n" + "="*60)
print("SUMMARY")
print(f"  Install:    {'OK' if ok else 'FAIL'}")
print(f"  Init DB:    {'OK' if ok2 else 'FAIL'}")
print(f"  App start:  {'OK' if ok3 else 'FAIL'}")
print(f"  Tests:      {'OK' if ok4 else 'FAIL'}")
print("="*60)
sys.exit(0 if (ok and ok2 and ok3 and ok4) else 1)
