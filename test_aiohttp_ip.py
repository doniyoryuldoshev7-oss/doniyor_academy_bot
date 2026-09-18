import asyncio
import aiohttp
import socket

async def main():
    timeout = aiohttp.ClientTimeout(total=15)

    connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        use_dns_cache=False
    )

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout
    ) as session:
        async with session.get(
            "https://149.154.166.110",
            headers={"Host": "api.telegram.org"}
        ) as r:
            print("STATUS:", r.status)
            print("OK")

asyncio.run(main())
