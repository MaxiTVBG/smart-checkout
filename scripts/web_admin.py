#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def main():
    print("Notice: The Smart Checkout Web Admin has been upgraded to a FastAPI architecture.")
    print("Redirecting to the new startup script: scripts/start_admin.py")
    print("-" * 60)

    script_path = Path(__file__).parent / "start_admin.py"
    try:
        subprocess.run([sys.executable, str(script_path)])
    except KeyboardInterrupt:
        print("\nWeb admin stopped.")
        return 130
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
