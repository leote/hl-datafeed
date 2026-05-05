import asyncio
import json
import websockets
import time
from datetime import datetime
from typing import Dict, List, Tuple

HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"

class HyperliquidBook:
    __slots__ = ('coin', 'bids', 'asks', 'best_bid', 'best_ask', 'mid',
                 'spread_pct', 'update_count', 'message_latency_ms', '_last_msg_time')

    def __init__(self, coin: str = "xyz:CL"):
        self.coin = coin
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.mid = 0.0
        self.spread_pct = 0.0
        self.update_count = 0
        self.message_latency_ms = 0.0
        self._last_msg_time = 0.0

    def update(self, levels: List) -> bool:
        now = time.perf_counter()

        if not levels or len(levels) < 2:
            return False

        if self._last_msg_time > 0:
            self.message_latency_ms = (now - self._last_msg_time) * 1000
        self._last_msg_time = now

        self.bids.clear()
        self.asks.clear()

        best_bid_temp = 0.0
        best_ask_temp = float('inf')
        has_data = False

        for bid in levels[0]:
            px = float(bid['px'])
            sz = float(bid['sz'])
            if sz > 0:
                self.bids[px] = sz
                if px > best_bid_temp:
                    best_bid_temp = px
                has_data = True

        for ask in levels[1]:
            px = float(ask['px'])
            sz = float(ask['sz'])
            if sz > 0:
                self.asks[px] = sz
                if px < best_ask_temp:
                    best_ask_temp = px
                has_data = True

        if not has_data or best_ask_temp == float('inf'):
            return False

        self.best_bid = best_bid_temp
        self.best_ask = best_ask_temp
        self.mid = (self.best_bid + self.best_ask) * 0.5
        self.spread_pct = ((self.best_ask - self.best_bid) / self.mid) * 100
        self.update_count += 1
        return True

    def get_market_state(self) -> Tuple[float, float, float, float]:
        return self.best_bid, self.best_ask, self.mid, self.spread_pct


async def websocket_handler(book: HyperliquidBook):
    reconnect_count = 0
    subscribe_msg = json.dumps({
        'method': 'subscribe',
        'subscription': {'type': 'l2Book', 'coin': book.coin}
    })

    while True:
        try:
            async with websockets.connect(
                HYPERLIQUID_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            ) as ws:
                await ws.send(subscribe_msg)

                if reconnect_count > 0:
                    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    print(f"[{now}] WebSocket reconnected")

                reconnect_count = 0

                async for message in ws:
                    try:
                        msg = json.loads(message)
                        if msg.get('channel') == 'l2Book':
                            book.update(msg['data'].get('levels', []))
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass

        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                ConnectionError):
            reconnect_count += 1
            delay = min(2 ** reconnect_count, 30)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n[{now}] WebSocket disconnected, reconnecting in {delay}s...")
            await asyncio.sleep(delay)
        except Exception as e:
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n[{now}] Connection error: {e}")
            await asyncio.sleep(5)


async def display_handler(book: HyperliquidBook, interval: float = 0.01):
    while True:
        await asyncio.sleep(interval)

        if not book.bids or not book.asks:
            continue

        bid, ask, mid, spread_pct = book.get_market_state()
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\r[{now}] BID: {bid:9.2f} | ASK: {ask:9.2f} | SPREAD: {spread_pct:6.4f}% | LAT: {book.message_latency_ms:5.0f}ms", end='', flush=True)


async def main():
    book = HyperliquidBook("xyz:CL")
    separator = "=" * 80
    print(separator)
    print(f"Starting datafeed for {book.coin}")
    print("Display interval: 10ms")
    print(separator)

    try:
        await asyncio.gather(
            websocket_handler(book),
            display_handler(book, interval=0.01)
        )
    except asyncio.CancelledError:
        pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSTOPPED")
