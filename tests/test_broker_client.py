"""BrokerGateClient must survive a connection burst that overflows the broker's accept
queue (the connection is reset *before* a grant — nothing to lose by retrying)."""
import asyncio
import json
import pytest

from service.broker_client import BrokerGateClient


async def _serve(path, drop_first: int):
    state = {"seen": 0, "granted": 0}

    async def handle(reader, writer):
        state["seen"] += 1
        await reader.readline()
        if state["seen"] <= drop_first:
            writer.transport.abort()                   # looks like ConnectionResetError client-side
            return
        writer.write((json.dumps({"status": "granted"}) + "\n").encode())
        await writer.drain()
        state["granted"] += 1
        await reader.readline()                        # release
        writer.close()

    return await asyncio.start_unix_server(handle, path=path), state


async def test_retries_connections_reset_before_the_grant(tmp_path):
    sock = str(tmp_path / "b.sock")
    server, state = await _serve(sock, drop_first=3)
    async with server:
        ran = []

        async def work():
            ran.append(1)
            return "ok"

        assert await BrokerGateClient("scraper", sock).run("rep", work) == "ok"
    assert ran == [1]                  # work ran exactly once, never retried
    assert state["seen"] == 4 and state["granted"] == 1


async def test_gives_up_with_a_clear_error_when_broker_is_down(tmp_path):
    client = BrokerGateClient("scraper", str(tmp_path / "missing.sock"))
    with pytest.raises(ConnectionError, match="is it running"):
        await client.run("rep", lambda: asyncio.sleep(0))
