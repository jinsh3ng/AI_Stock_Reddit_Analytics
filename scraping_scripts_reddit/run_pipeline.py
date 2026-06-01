import subprocess
import sys
import os
import requests
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def run_step(script_name, args=None):
    script_path = os.path.join(SCRIPT_DIR, script_name)
    cmd = [sys.executable, script_path] + (args or [])
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running: {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, capture_output=False, text=True, cwd=SCRIPT_DIR)

    if result.returncode != 0:
        print(f"❌ {script_name} failed with exit code {result.returncode}")
        sys.exit(1)

    print(f"✓ {script_name} completed successfully")

def refresh_api():
    api_url = os.getenv("API_URL")
    if not api_url:
        print("⚠ API_URL not set, skipping cache refresh")
        return
    try:
        res = requests.post(f"{api_url}/api/refresh")
        print(f"✓ API cache refreshed: {res.json()}")
    except Exception as e:
        print(f"⚠ Could not refresh API cache: {e}")

def main():
    print(f"\n{'#'*60}")
    print(f"AI STOCKS PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    run_step("step1_extract_urls.py")
    run_step("step2_extractcontent.py")
    run_step("step3_updatesql.py")
    run_step("step4_cleancontent_classify.py")

    refresh_api()

    print(f"\n{'#'*60}")
    print("ALL STEPS COMPLETE")
    print(f"{'#'*60}")

if __name__ == "__main__":
    main()