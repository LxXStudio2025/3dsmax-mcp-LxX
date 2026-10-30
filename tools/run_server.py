"""Launch from a checkout regardless of the client working directory."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    from max_mcp.server import main

    main()
