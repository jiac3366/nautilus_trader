import asyncio
import hashlib
import hmac
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from  typing import Literal

import msgspec

from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import Logger
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.nautilus_pyo3 import WebSocketClient
from nautilus_trader.core.nautilus_pyo3 import WebSocketClientError
from nautilus_trader.core.nautilus_pyo3 import WebSocketConfig


class OkxWebSocketClient:
    def __init__(
        self,
        clock: LiveClock,
        base_url: str,
        handler: Callable[[bytes], None],
        handler_reconnect: Callable[..., Awaitable[None]] | None,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        loop: asyncio.AbstractEventLoop,
        is_private: bool | None = False,
    ) -> None:
        self._clock = clock
        self._log: Logger = Logger(name=type(self).__name__)

        self._base_url: str = base_url
        self._handler: Callable[[bytes], None] = handler
        self._handler_reconnect: Callable[..., Awaitable[None]] | None = handler_reconnect
        self._loop = loop

        self._client: WebSocketClient | None = None
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._is_private = is_private
        self._is_running = False

        self._subscriptions: list[str] = []

    @property
    def subscriptions(self) -> list[str]:
        return self._subscriptions

    def has_subscription(self, item: str) -> bool:
        return item in self._subscriptions

    async def connect(self) -> None:
        self._is_running = True
        self._log.debug(f"Connecting to {self._base_url} websocket stream")
        config = WebSocketConfig(
            url=self._base_url,
            handler=self._handler,
            heartbeat=20,
            heartbeat_msg=msgspec.json.encode({"op": "ping"}).decode(),
            headers=[],
        )
        client = await WebSocketClient.connect(
            config=config,
            post_reconnection=self.reconnect,
        )
        self._client = client
        self._log.info(f"Connected to {self._base_url}", LogColor.BLUE)

        ## Authenticate
        if self._is_private:
            signature = self._get_signature()
            await self._send(signature)

    # TODO: Temporarily sync
    def reconnect(self) -> None:
        """
        Reconnect the client to the server and resubscribe to all streams.
        """
        if not self._is_running:
            return

        self._log.warning(f"Reconnected to {self._base_url}")

        # Re-subscribe to all streams
        self._loop.create_task(self._subscribe_all())

        if self._handler_reconnect:
            self._loop.create_task(self._handler_reconnect())  # type: ignore

    async def disconnect(self) -> None:
        self._is_running = False

        if self._client is None:
            self._log.warning("Cannot disconnect: not connected.")
            return

        try:
            await self._client.disconnect()
        except WebSocketClientError as e:
            self._log.error(str(e))

        self._client = None  # Dispose (will go out of scope)

        self._log.info(f"Disconnected from {self._base_url}", LogColor.BLUE)

    ################################################################################
    # Public
    ################################################################################

    async def subscribe_order_book(self, symbol: str, channel: str) -> None:
        if channel not in ("books", "books5", "bbo-tbt", "books50-l2-tbt", "books-l2-tbt"):
            raise ValueError(f"`channel` options are: 'books', 'books5', 'bbo-tbt', 'books50-l2-tbt', 'books-l2-tbt' but got '{channel}'")

        subscription = f"orderbook.{channel}.{symbol}"
        if subscription in self._subscriptions:
            self._log.warning(f"Cannot subscribe '{subscription}': already subscribed")
            return

        self._subscriptions.append(subscription)
        msg = {"op": "subscribe", "args": [subscription]}
        await self._send(msg)

    async def subscribe_trades(self, symbol: str) -> None:
        subscription = f"publicTrade.{symbol}"
        if subscription in self._subscriptions:
            self._log.warning(f"Cannot subscribe '{subscription}': already subscribed")
            return

        self._subscriptions.append(subscription)
        msg = {"op": "subscribe", "args": [subscription]}
        await self._send(msg)

    async def subscribe_tickers(self, symbol: str) -> None:
        subscription = f"tickers.{symbol}"
        if subscription in self._subscriptions:
            self._log.warning(f"Cannot subscribe '{subscription}': already subscribed")
            return

        self._subscriptions.append(subscription)
        msg = {"op": "subscribe", "args": [subscription]}
        await self._send(msg)

    async def subscribe_klines(self, symbol: str, interval: str) -> None:
        subscription = f"kline.{interval}.{symbol}"
        if subscription in self._subscriptions:
            self._log.warning(f"Cannot subscribe '{subscription}': already subscribed")
            return

        self._subscriptions.append(subscription)
        msg = {"op": "subscribe", "args": [subscription]}
        await self._send(msg)

    async def unsubscribe_order_book(self, symbol: str, depth: int) -> None:
        subscription = f"orderbook.{depth}.{symbol}"
        if subscription not in self._subscriptions:
            self._log.warning(f"Cannot unsubscribe '{subscription}': not subscribed")
            return

        self._subscriptions.remove(subscription)
        msg = {"op": "unsubscribe", "args": [subscription]}
        await self._send(msg)

    async def unsubscribe_trades(self, symbol: str) -> None:
        subscription = f"publicTrade.{symbol}"
        if subscription not in self._subscriptions:
            self._log.warning(f"Cannot unsubscribe '{subscription}': not subscribed")
            return

        self._subscriptions.remove(subscription)
        msg = {"op": "unsubscribe", "args": [subscription]}
        await self._send(msg)

    async def unsubscribe_tickers(self, symbol: str) -> None:
        subscription = f"tickers.{symbol}"
        if subscription not in self._subscriptions:
            self._log.warning(f"Cannot unsubscribe '{subscription}': not subscribed")
            return

        self._subscriptions.remove(subscription)
        msg = {"op": "unsubscribe", "args": [subscription]}
        await self._send(msg)

    async def unsubscribe_klines(self, symbol: str, interval: str) -> None:
        subscription = f"kline.{interval}.{symbol}"
        if subscription not in self._subscriptions:
            self._log.warning(f"Cannot unsubscribe '{subscription}': not subscribed")
            return

        self._subscriptions.remove(subscription)
        msg = {"op": "unsubscribe", "args": [subscription]}
        await self._send(msg)