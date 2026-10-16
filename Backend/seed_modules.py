"""
Load a customer's module taxonomy from a JSON file.

This is the ONBOARDING step for a project. Module names must be stable across meetings — the
measured alternative is 117 unique headings from four transcripts — so each customer gets a
fixed list, and requirements are filed into it rather than into names invented per call.

The taxonomy is PER CUSTOMER and never shared: a staffing client's modules are Orders and
Timesheets; a retail client's would be Checkout and Inventory.

Idempotent — existing module names are left alone, so re-running after editing the JSON adds
only what is new.

Usage (from Backend/):
    python seed_modules.py --list                                  # customers and their modules
    python seed_modules.py --customer-id <uuid>                    # uses module_taxonomy.json
    python seed_modules.py --customer-id <uuid> --file other.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.database import Customer  # noqa: E402
from app.services.module_registry import list_modules, seed_modules  # noqa: E402

DEFAULT_FILE = "module_taxonomy.json"


async def show_all() -> None:
    async with AsyncSessionLocal() as db:
        customers = (await db.execute(select(Customer))).scalars().all()
        if not customers:
            print("No customers found.")
            return
        for c in customers:
            modules = await list_modules(db, c.id, include_proposed=True)
            print(f"\n{c.name}   {c.id}")
            if not modules:
                print("    (no modules — run with --customer-id to seed)")
            for m in modules:
                flag = "" if m.status == "active" else f"  [{m.status}]"
                print(f"    {m.module_name}{flag}")
    await engine.dispose()


async def run_seed(customer_id: uuid.UUID, path: str) -> None:
    if not os.path.exists(path):
        print(f"Taxonomy file not found: {path}")
        raise SystemExit(1)

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    modules = data.get("modules", data if isinstance(data, list) else [])
    if not modules:
        print(f"No modules in {path}.")
        raise SystemExit(1)

    async with AsyncSessionLocal() as db:
        customer = (await db.execute(
            select(Customer).filter(Customer.id == customer_id)
        )).scalars().first()
        if not customer:
            print(f"Customer {customer_id} not found. Run with --list to see the options.")
            raise SystemExit(1)

        print(f"Customer : {customer.name}")
        print(f"Taxonomy : {path}  ({len(modules)} modules)")
        created, skipped = await seed_modules(db, customer_id, modules)
        print(f"\n  created {created}, already present {skipped}")

        print("\nModules now defined:")
        for m in await list_modules(db, customer_id, include_proposed=True):
            flag = "" if m.status == "active" else f"  [{m.status}]"
            print(f"    {m.module_name}{flag}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="show customers and their modules")
    parser.add_argument("--customer-id", help="customer to seed")
    parser.add_argument("--file", default=DEFAULT_FILE, help=f"taxonomy JSON (default {DEFAULT_FILE})")
    args = parser.parse_args()

    if args.list or not args.customer_id:
        asyncio.run(show_all())
        if not args.list:
            print("\nPass --customer-id <uuid> to seed one of these.")
        return

    asyncio.run(run_seed(uuid.UUID(args.customer_id), args.file))


if __name__ == "__main__":
    main()
