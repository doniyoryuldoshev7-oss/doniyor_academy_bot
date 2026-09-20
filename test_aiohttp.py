import asyncio
import aiohttp

async def main():
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://api.telegram.org") as r:
            print("STATUS:", r.status)
            print("OK")

asyncio.run(main())
