#!/usr/bin/env python3
"""TCP relay: listens on localhost:2283, forwards to Immich on the LAN.

Docker Desktop for Mac cannot route to LAN IPs from containers.
This relay runs on the Mac host so containers can reach Immich via
host.docker.internal:2283.

Usage:
    python3 scripts/immich-relay.py &
    # or: nohup python3 scripts/immich-relay.py &
"""
from __future__ import annotations

import asyncio
import sys

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 2283
REMOTE_HOST = "192.168.1.229"
REMOTE_PORT = 2283


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        writer.close()


async def handle(local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
    try:
        remote_reader, remote_writer = await asyncio.open_connection(REMOTE_HOST, REMOTE_PORT)
    except OSError as e:
        print(f"Cannot connect to {REMOTE_HOST}:{REMOTE_PORT}: {e}", file=sys.stderr)
        local_writer.close()
        return
    await asyncio.gather(
        relay(local_reader, remote_writer),
        relay(remote_reader, local_writer),
    )


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    print(f"Immich relay listening on {LISTEN_HOST}:{LISTEN_PORT} -> {REMOTE_HOST}:{REMOTE_PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
