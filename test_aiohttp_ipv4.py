import asyncio
import aiohttp
import socket

async def main():
    timeout = aiohttp.ClientTimeout(total=15)

    connector = aiohttp.TCPConnector(
        family=socket.AF_INET
    )

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout
    ) as session:
        async with session.get("https://api.telegram.org") as r:
            print("STATUS:", r.status)
            print("OK")

asyncio.run(main())
