import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.future import select
from app.core.database import AsyncSessionLocal
from app.models.database import ModuleDocument

async def main():
    async with AsyncSessionLocal() as session:
        mods = (await session.execute(
            select(ModuleDocument.module_name, ModuleDocument.status, ModuleDocument.description)
        )).all()
        print(f"Total modules in DB: {len(mods)}")
        for m in mods:
            print(f"[{m[1]:<10}] {m[0]:<45} : {m[2]}")

if __name__ == "__main__":
    asyncio.run(main())
