import asyncio
import json
import websockets
import time
import datetime
from typing import Dict, List, Tuple

HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"

class HyperliquidBook:
    __slots__ = ('coin', 'bids', 'asks', 'best_bid', 'best_ask', 'mid', 
                 'spread_pct', 'last_update', 'update_count', 
                 'last_message_time', 'message_latency_ms')
    
    def __init__(self, coin: str = "BTC"):
        self.coin = coin
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.mid = 0.0
        self.spread_pct = 0.0
        self.last_update = 0.0
        self.update_count = 0
        self.last_message_time = 0.0
        self.message_latency_ms = 0.0
        
    def update(self, levels: List) -> bool:
        message_received_time = time.time()
        
        if not levels or len(levels) < 2:
            return False
            
        if self.last_message_time > 0:
            self.message_latency_ms = (message_received_time - self.last_message_time) * 1000
        
        self.last_message_time = message_received_time
        
        # clear old data
        self.bids.clear()
        self.asks.clear()
        
        # process bids
        for bid in levels[0]:
            px = float(bid['px'])
            sz = float(bid['sz'])
            if sz > 0:
                self.bids[px] = sz
        
        # process asks
        for ask in levels[1]:
            px = float(ask['px'])
            sz = float(ask['sz'])
            if sz > 0:
                self.asks[px] = sz
        
        if not self.bids or not self.asks:
            return False
            
        # update market data
        self.best_bid = max(self.bids.keys())
        self.best_ask = min(self.asks.keys())
        self.mid = (self.best_bid + self.best_ask) * 0.5
        self.spread_pct = ((self.best_ask - self.best_bid) / self.mid) * 100
        self.last_update = time.time()
        self.update_count += 1

        return True

    def get_market_state(self) -> Tuple[float, float, float, float]:
        return self.best_bid, self.best_ask, self.mid, self.spread_pct


async def websocket_handler(book: HyperliquidBook):
    reconnect_count = 0
    
    while True:
        try:
            async with websockets.connect(
                HYPERLIQUID_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            ) as ws:
                subscribe_msg = {
                    'method': 'subscribe',
                    'subscription': {'type': 'l2Book', 'coin': book.coin}
                }
                await ws.send(json.dumps(subscribe_msg))
                
                if reconnect_count > 0:
                    current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
                    print(f"[{current_time}] WebSocket reconnected")
                
                reconnect_count = 0
                
                async for message in ws:
                    try:
                        msg = json.loads(message)
                        if msg.get('channel') == 'l2Book':
                            book.update(msg['data'].get('levels', []))
                    except json.JSONDecodeError:
                        continue
                    except KeyError:
                        continue
                        
        except (websockets.exceptions.ConnectionClosed, 
                websockets.exceptions.WebSocketException,
                ConnectionError):
            reconnect_count += 1
            current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"[{current_time}] WebSocket disconnected, reconnecting...")
            await asyncio.sleep(min(2 ** reconnect_count, 30))
        except Exception as e:
            current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"[{current_time}] Error: {e}")
            await asyncio.sleep(5)


async def display_handler(book: HyperliquidBook, interval: float = 0.1):
    while True:
        await asyncio.sleep(interval)
        
        if not book.bids or not book.asks:
            continue
            
        bid, ask, mid, spread_pct = book.get_market_state()
        
        current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        
        print(f"[{current_time}] "
              f"BID: {bid:9.2f} | " # bid
              f"ASK: {ask:9.2f} | " # ask
              f"SPREAD: {spread_pct:6.4f}% | " # spread percentage
              f"LAT: {book.message_latency_ms:5.0f}ms") # time inbetween ws messages


async def main():
    book = HyperliquidBook("BTC")
    
    current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    print("=" * 80)
    print(f"[{current_time}] Starting orderbook monitor for {book.coin}")
    print("Display interval: 10ms")
    print("=" * 80)

    try:
        await asyncio.gather(
            websocket_handler(book),
            display_handler(book, interval=0.01) # 10ms interval
        )
    except asyncio.CancelledError:
        pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]

        print(f"\n[{current_time}] STOPPED")
