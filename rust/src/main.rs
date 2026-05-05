use futures_util::{SinkExt, StreamExt};
use std::collections::BTreeMap;
use std::io::Write;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;
use tokio::time;
use tokio_tungstenite::{connect_async, tungstenite::Message};

const HYPERLIQUID_WS_URL: &str = "wss://api.hyperliquid.xyz/ws";

#[derive(Debug)]
struct OrderBook {
    coin: String,
    bids: BTreeMap<ordered_float::OrderedFloat<f64>, f64>,
    asks: BTreeMap<ordered_float::OrderedFloat<f64>, f64>,
    best_bid: f64,
    best_ask: f64,
    spread_pct: f64,
    last_message_time: Option<Instant>,
    message_latency_ms: f64,
}

impl OrderBook {
    fn new(coin: &str) -> Self {
        Self {
            coin: coin.to_string(),
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            best_bid: 0.0,
            best_ask: 0.0,
            spread_pct: 0.0,
            last_message_time: None,
            message_latency_ms: 0.0,
        }
    }

    fn update(&mut self, levels: &[serde_json::Value]) -> bool {
        let now = Instant::now();
        
        if let Some(last_time) = self.last_message_time {
            self.message_latency_ms = now.duration_since(last_time).as_secs_f64() * 1000.0;
        }
        self.last_message_time = Some(now);

        if levels.len() < 2 {
            return false;
        }

        self.bids.clear();
        self.asks.clear();

        if let Some(bids) = levels[0].as_array() {
            for bid in bids {
                if let (Some(px_str), Some(sz_str)) = (bid["px"].as_str(), bid["sz"].as_str()) {
                    if let (Ok(px), Ok(sz)) = (px_str.parse::<f64>(), sz_str.parse::<f64>()) {
                        if sz > 0.0 {
                            self.bids.insert(ordered_float::OrderedFloat(px), sz);
                        }
                    }
                }
            }
        }

        if let Some(asks) = levels[1].as_array() {
            for ask in asks {
                if let (Some(px_str), Some(sz_str)) = (ask["px"].as_str(), ask["sz"].as_str()) {
                    if let (Ok(px), Ok(sz)) = (px_str.parse::<f64>(), sz_str.parse::<f64>()) {
                        if sz > 0.0 {
                            self.asks.insert(ordered_float::OrderedFloat(px), sz);
                        }
                    }
                }
            }
        }

        if self.bids.is_empty() || self.asks.is_empty() {
            return false;
        }

        if let Some((best_bid_px, _)) = self.bids.iter().rev().next() {
            self.best_bid = best_bid_px.0;
        }
        
        if let Some((best_ask_px, _)) = self.asks.iter().next() {
            self.best_ask = best_ask_px.0;
        }

        let mid = (self.best_bid + self.best_ask) * 0.5;
        if mid > 0.0 {
            self.spread_pct = ((self.best_ask - self.best_bid) / mid) * 100.0;
        }

        true
    }

    fn has_data(&self) -> bool {
        !self.bids.is_empty() && !self.asks.is_empty()
    }
}

async fn websocket_handler(book: Arc<RwLock<OrderBook>>) {
    let mut reconnect_count = 0u32;
    let coin = book.read().await.coin.clone();

    loop {
        match connect_async(HYPERLIQUID_WS_URL).await {
            Ok((ws_stream, _)) => {
                let (mut write, mut read) = ws_stream.split();

                let subscribe_msg = serde_json::json!({
                    "method": "subscribe",
                    "subscription": {
                        "type": "l2Book",
                        "coin": coin
                    }
                });
                
                if let Err(e) = write.send(Message::Text(subscribe_msg.to_string())).await {
                    eprintln!("Failed to send subscription: {}", e);
                    continue;
                }

                if reconnect_count > 0 {
                    let now = chrono::Local::now().format("%H:%M:%S%.3f");
                    println!("[{}] WebSocket reconnected", now);
                }
                reconnect_count = 0;

                while let Some(msg_result) = read.next().await {
                    match msg_result {
                        Ok(msg) => {
                            if let Ok(text) = msg.into_text() {
                                if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                                    if parsed["channel"] == "l2Book" {
                                        if let Some(levels) = parsed["data"]["levels"].as_array() {
                                            book.write().await.update(levels);
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("WebSocket error: {}", e);
                            break;
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("Connection error: {}", e);
            }
        }

        reconnect_count += 1;
        let delay = std::cmp::min(2u32.pow(reconnect_count), 30);
        let now = chrono::Local::now().format("%H:%M:%S%.3f");
        println!("[{}] WebSocket disconnected, reconnecting in {}s...", now, delay);
        time::sleep(Duration::from_secs(delay as u64)).await;
    }
}

async fn display_handler(book: Arc<RwLock<OrderBook>>) {
    let mut interval = time::interval(Duration::from_millis(10));

    loop {
        interval.tick().await;
        let book_guard = book.read().await;

        if !book_guard.has_data() {
            continue;
        }

        let now = chrono::Local::now().format("%H:%M:%S%.3f");
        print!(
            "\r[{}] BID: {:9.2} | ASK: {:9.2} | SPREAD: {:6.4}% | LAT: {:5.0}ms",
            now,
            book_guard.best_bid,
            book_guard.best_ask,
            book_guard.spread_pct,
            book_guard.message_latency_ms
        );
        let _ = std::io::stdout().flush();
    }
}

#[tokio::main]
async fn main() {
    let book = Arc::new(RwLock::new(OrderBook::new("xyz:CL"))); // change coin if you want e.g. new("BTC")
    let coin = "xyz:CL";

    println!("{}", "=".repeat(80));
    println!("Starting datafeed for {}", coin);
    println!("Display interval: 10ms");
    println!("{}", "=".repeat(80));

    let ws_book = Arc::clone(&book);
    let display_book = Arc::clone(&book);

    tokio::select! {
        _ = websocket_handler(ws_book) => {},
        _ = display_handler(display_book) => {},
        _ = tokio::signal::ctrl_c() => {
            println!("\nSTOPPED");
        }
    }
}