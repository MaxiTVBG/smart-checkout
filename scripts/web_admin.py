#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path

def main():
    print("Notice: The Smart Checkout Web Admin has been upgraded to a FastAPI architecture.")
    print("Redirecting to the new startup script: scripts/start_admin.py")
    print("-" * 60)
    
    script_path = Path(__file__).parent / "start_admin.py"
    subprocess.run([sys.executable, str(script_path)])

if __name__ == "__main__":
    main()
