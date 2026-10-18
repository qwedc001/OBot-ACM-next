"""One-host HTTPS proxy for external CLIs using standard proxy variables.

TLS stays end-to-end: this only forwards bytes to a previously validated public
IP. DNS is never resolved again by the child, and cross-host redirects cannot
open a tunnel. It does not import, monkey-patch or rewrite the external tool.
"""

import asyncio
import ipaddress
from contextlib import asynccontextmanager, suppress


@asynccontextmanager
async def pinned_https_proxy(host: str, addresses: list[str]):
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("A public address is required")
    active = set()
    remaining = 64 * 1024 * 1024
    authority = f"[{host}]:443" if ":" in host else f"{host}:443"

    async def connect(reader, writer):
        task = asyncio.current_task()
        active.add(task)
        upstream = None
        pumps = []
        try:
            async with asyncio.timeout(55):
                headers = await reader.readuntil(b"\r\n\r\n")
                method, target, protocol = headers.split(b"\r\n", 1)[0].decode("ascii").split()
                if method != "CONNECT" or target.lower() != authority.lower():
                    writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    await writer.drain()
                    return
                remote, upstream = await asyncio.open_connection(addresses[0], 443)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()

                async def pump(source, destination):
                    nonlocal remaining
                    while chunk := await source.read(65536):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError("CLI transfer budget exceeded")
                        destination.write(chunk)
                        await destination.drain()

                pumps = [
                    asyncio.create_task(pump(reader, upstream)),
                    asyncio.create_task(pump(remote, writer)),
                ]
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass  # CLI receives a closed connection, never host/IP diagnostics.
        finally:
            for pump_task in pumps:
                pump_task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, upstream):
                if stream:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            active.discard(task)

    server = await asyncio.start_server(connect, "127.0.0.1", 0, limit=8192)
    port = server.sockets[0].getsockname()[1]
    try:
        yield {
            "HTTPS_PROXY": f"http://127.0.0.1:{port}",
            "HTTP_PROXY": f"http://127.0.0.1:{port}",
            "NO_PROXY": "",
        }
    finally:
        server.close()
        await server.wait_closed()
        tasks = list(active)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
