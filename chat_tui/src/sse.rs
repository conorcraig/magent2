use std::mem;
use tokio::sync::mpsc::UnboundedSender;
use serde_json::Value;
use futures_util::StreamExt;
use crate::UiEvent;

/// WHATWG-compliant SSE event parser
/// Accumulates multi-line data until blank line, handles id/event/retry fields
pub struct SseParser {
    current_event: SseEvent,
    in_data: bool,
}

#[derive(Default, Debug, PartialEq)]
struct SseEvent {
    id: Option<String>,
    event: Option<String>,
    retry: Option<String>,
    data: String,
}

impl SseParser {
    pub fn new() -> Self {
        Self {
            current_event: SseEvent::default(),
            in_data: false,
        }
    }

    /// Process a line of SSE data
    /// Returns Some(event) when a complete event is parsed (on blank line)
    pub fn process_line(&mut self, line: &str) -> Option<SseEvent> {
        let line = line.trim_end_matches('\r');

        // Comment lines are ignored
        if line.starts_with(':') {
            return None;
        }

        // Empty line ends the current event
        if line.is_empty() {
            if !self.current_event.data.is_empty() || self.current_event.id.is_some()
                || self.current_event.event.is_some() || self.current_event.retry.is_some() {
                let event = mem::take(&mut self.current_event);
                return Some(event);
            }
            return None;
        }

        // Field lines
        if let Some(field_value) = line.strip_prefix("id: ") {
            self.current_event.id = Some(field_value.trim().to_string());
        } else if let Some(field_value) = line.strip_prefix("event: ") {
            self.current_event.event = Some(field_value.trim().to_string());
        } else if let Some(field_value) = line.strip_prefix("retry: ") {
            self.current_event.retry = Some(field_value.trim().to_string());
        } else if let Some(data_value) = line.strip_prefix("data: ") {
            if !self.current_event.data.is_empty() {
                self.current_event.data.push('\n');
            }
            self.current_event.data.push_str(data_value);
            self.in_data = true;
        } else if line.starts_with("data:") {
            // Handle "data:" with no space (invalid but possible)
            if !self.current_event.data.is_empty() {
                self.current_event.data.push('\n');
            }
            self.current_event.data.push_str(&line[5..]);
            self.in_data = true;
        } else if self.in_data {
            // Continuation of multi-line data
            self.current_event.data.push('\n');
            self.current_event.data.push_str(line);
        }

        None
    }

    /// Check if we're currently accumulating data
    pub fn has_pending_data(&self) -> bool {
        !self.current_event.data.is_empty()
    }
}

impl SseEvent {
    pub fn dispatch_to_ui(&self, idx: usize, gen: u64, tx: &UnboundedSender<UiEvent>) {
        // Only dispatch if we have data
        if self.data.is_empty() {
            return;
        }

        // Parse the JSON data
        if let Ok(v) = serde_json::from_str::<Value>(&self.data) {
            let event_type = v.get("event").and_then(|x| x.as_str()).unwrap_or("");

            match event_type {
                "token" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::ModelToken {
                            idx,
                            gen,
                            text: text.to_string(),
                        });
                    }
                }
                "output" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::ModelOutput {
                            idx,
                            gen,
                            text: text.to_string(),
                        });
                    }
                }
                "tool_step" => {
                    let name = v.get("name").and_then(|x| x.as_str()).unwrap_or("tool");
                    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
                    let summary = v.get("result_summary")
                        .and_then(|x| x.as_str())
                        .unwrap_or("");
                    let line = if summary.is_empty() {
                        format!("[tool:{}] {}", status, name)
                    } else {
                        format!("[tool:{}] {}: {}", status, name, summary)
                    };
                    let _ = tx.send(UiEvent::ToolStep {
                        idx,
                        gen,
                        name: name.to_string(),
                        status: status.to_string(),
                        summary: if summary.is_empty() {
                            None
                        } else {
                            Some(summary.to_string())
                        },
                    });
                    let _ = tx.send(UiEvent::Tool {
                        idx,
                        gen,
                        text: line,
                    });
                }
                "user_message" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::User {
                            idx,
                            gen,
                            text: text.to_string(),
                        });
                    }
                }
                _ => {}
            }
        }
    }

    pub fn get_id(&self) -> Option<&str> {
        self.id.as_deref()
    }
}

/// Unified SSE streaming task
/// Replaces both spawn_sse_task and inline SSE handling
pub async fn spawn_unified_sse_task(
    base_url: String,
    conversation_id: String,
    resume_id: Option<String>,
    idx: usize,
    gen: u64,
    tx: UnboundedSender<UiEvent>,
    client: reqwest::Client,
) {
    let url = format!("{}/stream/{}", base_url, conversation_id);

    let mut req = client
        .get(&url)
        .header("Accept", "text/event-stream");

    if let Some(id) = &resume_id {
        if !id.is_empty() {
            req = req.header("Last-Event-ID", id);
        }
    }

    match req.send().await {
        Ok(resp) => {
            if resp.status().is_success() {
                let mut bytes_stream = resp.bytes_stream();
                let mut buf: Vec<u8> = Vec::new();
                let mut parser = SseParser::new();
                let mut errored = false;

                while let Some(item) = bytes_stream.next().await {
                    match item {
                        Ok(chunk) => {
                            for &b in &chunk {
                                if b == b'\n' {
                                    // Use mem::take instead of clone for efficiency
                                    let line_bytes = mem::take(&mut buf);
                                    if let Ok(line) = String::from_utf8(line_bytes) {
                                        if let Some(event) = parser.process_line(&line) {
                                            // Send the Last-Event-ID update
                                            if let Some(id) = event.get_id() {
                                                let _ = tx.send(UiEvent::SetLastId {
                                                    idx,
                                                    gen,
                                                    id: id.to_string(),
                                                });
                                            }
                                            event.dispatch_to_ui(idx, gen, &tx);
                                        }
                                    }
                                } else {
                                    buf.push(b);
                                }
                            }
                        }
                        Err(_) => {
                            errored = true;
                            let _ = tx.send(UiEvent::StreamError {
                                idx,
                                gen,
                                message: "stream interrupted".to_string(),
                            });
                            break;
                        }
                    }
                }

                if !errored {
                    let _ = tx.send(UiEvent::StreamClosed { idx, gen });
                }
            } else {
                let status = resp.status();
                let _ = tx.send(UiEvent::StreamError {
                    idx,
                    gen,
                    message: format!("stream failed with status {}", status),
                });
            }
        }
        Err(_) => {
            let _ = tx.send(UiEvent::StreamError {
                idx,
                gen,
                message: "stream request failed".to_string(),
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sse_parser_single_line() {
        let mut parser = SseParser::new();
        assert_eq!(parser.process_line("data: test"), None);
        assert_eq!(parser.process_line(""), Some(SseEvent {
            data: "test".to_string(),
            ..Default::default()
        }));
    }

    #[test]
    fn test_sse_parser_multi_line() {
        let mut parser = SseParser::new();
        assert_eq!(parser.process_line("data: line1"), None);
        assert_eq!(parser.process_line("data: line2"), None);
        assert_eq!(parser.process_line(""), Some(SseEvent {
            data: "line1\nline2".to_string(),
            ..Default::default()
        }));
    }

    #[test]
    fn test_sse_parser_with_id_and_event() {
        let mut parser = SseParser::new();
        assert_eq!(parser.process_line("id: 123"), None);
        assert_eq!(parser.process_line("event: token"), None);
        assert_eq!(parser.process_line("data: test"), None);
        assert_eq!(parser.process_line(""), Some(SseEvent {
            id: Some("123".to_string()),
            event: Some("token".to_string()),
            data: "test".to_string(),
            ..Default::default()
        }));
    }

    #[test]
    fn test_sse_parser_comment_ignored() {
        let mut parser = SseParser::new();
        assert_eq!(parser.process_line(": this is a comment"), None);
        assert_eq!(parser.process_line("data: test"), None);
        assert_eq!(parser.process_line(""), Some(SseEvent {
            data: "test".to_string(),
            ..Default::default()
        }));
    }

    #[test]
    fn test_sse_parser_data_without_space() {
        let mut parser = SseParser::new();
        assert_eq!(parser.process_line("data:line1"), None);
        assert_eq!(parser.process_line("data:line2"), None);
        assert_eq!(parser.process_line(""), Some(SseEvent {
            data: "line1\nline2".to_string(),
            ..Default::default()
        }));
    }
}
