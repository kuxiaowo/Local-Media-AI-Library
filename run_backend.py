from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    backend_dir = root / "backend"
    os.chdir(backend_dir)
    sys.path.insert(0, str(backend_dir))

    import uvicorn
    from app.config import get_settings

    settings = get_settings()
    migration_result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend_dir)
    if migration_result.returncode != 0:
        return migration_result.returncode

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        reload_dirs=[str(backend_dir / "app")],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
