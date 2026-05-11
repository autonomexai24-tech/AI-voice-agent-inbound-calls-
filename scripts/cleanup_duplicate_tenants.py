"""Clean duplicate tenant slug rows and ensure the unique slug index exists."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db.connection import close_pool, init_pool
from backend.db.tenants import cleanup_duplicate_tenant_slugs, ensure_tenants_schema
from backend.logging import configure_logging


def main() -> None:
    load_dotenv()
    configure_logging("tenant-cleanup")
    init_pool()
    try:
        deleted_count = cleanup_duplicate_tenant_slugs()
        ensure_tenants_schema()
        print(f"duplicate.tenants.cleaned deleted_count={deleted_count}")
        print("unique.index.created index=idx_tenants_slug_lower")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
