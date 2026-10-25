from __future__ import annotations

import asyncio

from reelvault.main import ignore_client_resets


class Transport:
    def _call_connection_lost(self, exc: BaseException | None) -> None:
        raise ConnectionResetError(10054, "reset by peer")


def test_proactor_connection_resets_are_not_logged_as_errors():
    loop = asyncio.new_event_loop()
    seen: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    ignore_client_resets(loop)
    try:
        transport = Transport()
        # The same callback shape the Windows proactor loop schedules.
        loop.call_soon(transport._call_connection_lost, None)
        loop.call_soon(lambda: (_ for _ in ()).throw(ConnectionResetError()))
        loop.call_soon(loop.stop)
        loop.run_forever()
    finally:
        loop.close()
    # Only the unrelated reset still reaches the previous handler.
    assert len(seen) == 1 and "_call_connection_lost" not in repr(seen[0]["handle"])
