"""`python -m app`: start TadDee on 0.0.0.0:$NEST_PORT (default 8040).
Data folder: $NEST_DATA_DIR (default ./data)."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("NEST_PORT") or 8040)
    host = os.environ.get("NEST_HOST") or "0.0.0.0"
    from .main import create_app
    print(f"TadDee: open http://localhost:{port} (data folder: "
          f"{os.path.abspath(os.environ.get('NEST_DATA_DIR') or './data')})")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
