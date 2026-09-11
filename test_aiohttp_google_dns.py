import asyncio
import aiohttp
from aiohttp.resolver import AsyncResolver

async def main():
    resolver = AsyncResolver(nameservers=["8.8.8.8"])
    connector = aiohttp.TCPConnector(resolver=resolver)

    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get("https://api.telegram.org", timeout=15) as r:
            print("STATUS:", r.status)
            print("OK")

asyncio.run(main())
