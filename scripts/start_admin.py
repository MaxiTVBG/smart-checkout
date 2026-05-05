#!/usr/bin/env python3
import uvicorn
import yaml
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import uvicorn
import yaml

def main():
    config_file = Path("config.yaml")
    host = "0.0.0.0"
    port = 8000
    
    if config_file.exists():
        try:
            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            web_config = config.get("web_admin", {})
            host = web_config.get("host", host)
            port = web_config.get("port", port)
        except Exception as e:
            print(f"Failed to read config: {e}")
            
    print(f"Starting Smart Checkout FastAPI Admin on http://{host}:{port}")
    uvicorn.run("src.web.app:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
