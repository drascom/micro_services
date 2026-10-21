use axum::extract::ws::{Message, WebSocket};
use chrono::Utc;
use inet_viewer_shared::Event;
use serde_json::Value;
use tokio::sync::broadcast;

#[derive(Clone, Debug)]
pub struct EventBus {
    sender: broadcast::Sender<Event>,
}

impl Default for EventBus {
    fn default() -> Self {
        let (sender, _) = broadcast::channel(256);
        Self { sender }
    }
}

impl EventBus {
    pub fn publish(&self, event_type: impl Into<String>, data: Value) {
        let _ = self.sender.send(Event {
            event_type: event_type.into(),
            occurred_at: Utc::now(),
            data,
        });
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.sender.subscribe()
    }
}

pub async fn serve_socket(mut socket: WebSocket, bus: EventBus) {
    let mut receiver = bus.subscribe();
    loop {
        match receiver.recv().await {
            Ok(event) => {
                let Ok(payload) = serde_json::to_string(&event) else {
                    continue;
                };
                if socket.send(Message::Text(payload.into())).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Lagged(skipped)) => {
                bus.publish(
                    "snapshot.updated",
                    serde_json::json!({"reason":"lagged","skipped":skipped}),
                );
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}
