use std::io::{self, Write};
use std::process::Command;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crossterm::event::{
    self, DisableBracketedPaste, EnableBracketedPaste, Event as CEvent, KeyCode, KeyEvent,
    KeyboardEnhancementFlags, PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
};
use crossterm::terminal::ScrollUp;
use crossterm::{cursor, execute};
use ratatui::prelude::*;
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Paragraph, Tabs, Wrap};
use reqwest::{Client, StatusCode};
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio::task::JoinHandle;
// serde_json used via fully qualified path in parsing; no direct import needed
use pulldown_cmark::{Event as MdEvent, Options as MdOptions, Parser as MdParser, Tag, TagEnd};
use serde::Deserialize;
use unicode_width::UnicodeWidthStr;

mod sse;

/// Create a shared HTTP client with proper configuration
/// - Connection timeout for reliability
/// - TCP keepalive to maintain connections
/// - No global timeout (important for SSE streams)
fn create_http_client() -> Client {
    Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .tcp_keepalive(Duration::from_secs(60))
        .build()
        .expect("Failed to create HTTP client")
}

const SPINNER_FRAMES: [&str; 4] = [".", "..", "...", ".."];
const AGENTS_REFRESH_MS: u64 = 3_000;
const GRAPH_REFRESH_MS: u64 = 5_000;
const GRAPH_EDGE_LIMIT: usize = 120;

// UI event bus carrying structured events to the render loop.
enum UiEvent {
    User {
        idx: usize,
        gen: u64,
        text: String,
    },
    ModelToken {
        idx: usize,
        gen: u64,
        text: String,
    },
    ModelOutput {
        idx: usize,
        gen: u64,
        text: String,
    },
    Tool {
        idx: usize,
        gen: u64,
        text: String,
    },
    ToolStep {
        idx: usize,
        gen: u64,
        name: String,
        status: String,
        summary: Option<String>,
    },
    StreamError {
        idx: usize,
        gen: u64,
        message: String,
    },
    StreamClosed {
        idx: usize,
        gen: u64,
    },
    SetLastId {
        idx: usize,
        gen: u64,
        id: String,
    },
}

#[derive(Clone, Copy)]
enum Speaker {
    User,
    Model,
    Tool,
}

struct Message {
    speaker: Speaker,
    content: String,
}

enum BusyReason {
    WaitingResponse,
    Tool {
        name: String,
        status: String,
        summary: Option<String>,
    },
    Error {
        message: String,
    },
}

struct BusyState {
    since: Instant,
    reason: BusyReason,
}

struct AgentRow {
    name: String,
    active_runs: u64,
    last_seen: Option<SystemTime>,
    recent_conversations: usize,
}

struct GraphNode {
    id: String,
    kind: String,
}

struct GraphEdge {
    from: String,
    to: String,
    count: i64,
}

struct GraphData {
    nodes: Vec<GraphNode>,
    edges: Vec<GraphEdge>,
    omitted_edges: usize,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum FocusTarget {
    Input,
    Conversations,
}

struct ChatSession {
    title: String,
    messages: Vec<Message>,
    input: String,
    gen: u64, // increments each time user sends; filters stale stream tasks
    last_sse_id: Option<String>,
    scroll: u16,
    max_scroll: u16,
    viewport_height: u16,
    follow: bool,
    stream_task: Option<JoinHandle<()>>, // abort previous SSE task on new send
    conversation_id: Option<String>,     // None until first send, then set to new id
    busy: Option<BusyState>,
}

struct AppState {
    sessions: Vec<ChatSession>,
    active: usize,
    rx: UnboundedReceiver<UiEvent>,
    tx: UnboundedSender<UiEvent>,
    base_url: String,
    gateway_ok: bool,
    agent_name: String,
    show_conversations: bool,
    conversations: Vec<String>,
    conversations_selected: usize,
    show_agents: bool,
    agents: Vec<AgentRow>,
    agents_last_fetch: Option<Instant>,
    agents_error: Option<String>,
    show_graph: bool,
    graph: Option<GraphData>,
    graph_for_conversation: Option<String>,
    graph_last_fetch: Option<Instant>,
    graph_error: Option<String>,
    focus: FocusTarget,
    http_client: Client,
}

impl ChatSession {
    fn set_busy(&mut self, reason: BusyReason) {
        let since = match (&self.busy, &reason) {
            (Some(existing), BusyReason::Tool { name: new_name, .. }) => match &existing.reason {
                BusyReason::Tool { name: old_name, .. } if old_name == new_name => existing.since,
                _ => Instant::now(),
            },
            (Some(existing), BusyReason::WaitingResponse)
                if matches!(existing.reason, BusyReason::WaitingResponse) =>
            {
                existing.since
            }
            _ => Instant::now(),
        };
        self.busy = Some(BusyState { since, reason });
    }

    fn clear_busy(&mut self) {
        self.busy = None;
    }
}

impl AppState {
    fn new() -> Self {
        let (tx, rx) = mpsc::unbounded_channel();
        let http_client = create_http_client();
        // Determine base URL (auto-discover via docker compose if requested)
        let env_base = std::env::var("MAGENT2_BASE_URL").unwrap_or_else(|_| "auto".to_string());
        let base_url = if env_base.to_lowercase() == "auto" {
            discover_base_url()
        } else {
            env_base
        };

        Self {
            sessions: vec![ChatSession {
                title: "Chat 1".to_string(),
                messages: Vec::new(),
                input: String::new(),
                gen: 0,
                last_sse_id: None,
                scroll: 0,
                max_scroll: 0,
                viewport_height: 0,
                follow: true,
                stream_task: None,
                conversation_id: None,
                busy: None,
            }],
            active: 0,
            rx,
            tx,
            base_url,
            gateway_ok: false,
            agent_name: std::env::var("MAGENT2_AGENT_NAME")
                .unwrap_or_else(|_| "DevAgent".to_string()),
            show_conversations: false,
            conversations: Vec::new(),
            conversations_selected: 0,
            show_agents: false,
            agents: Vec::new(),
            agents_last_fetch: None,
            agents_error: None,
            show_graph: false,
            graph: None,
            graph_for_conversation: None,
            graph_last_fetch: None,
            graph_error: None,
            focus: FocusTarget::Input,
            http_client,
        }
    }
}

// Minimal SSE JSON payload structure is handled dynamically in handle_sse_line

#[tokio::main]
async fn main() -> std::io::Result<()> {
    // Preserve scrollback (scroll up to row 0) before entering alt screen
    pre_init_terminal()?;
    // Install panic hook to restore terminal and flags on crash
    install_panic_hook();
    // Enter alt screen / raw mode via ratatui
    let mut terminal = ratatui::init();
    // Enable bracketed paste and enhanced keyboard flags for better UX
    enable_terminal_features()?;
    let mut app = AppState::new();
    let mut last_health = Instant::now() - Duration::from_secs(2);

    loop {
        // Periodic gateway health probe (best-effort)
        if last_health.elapsed() >= Duration::from_millis(750) {
            let url = format!("{}/health", app.base_url);
            let ok = match app.http_client
                .get(&url)
                .timeout(Duration::from_millis(1200))
                .send()
                .await
            {
                Ok(resp) => resp.status().is_success(),
                Err(_) => false,
            };
            app.gateway_ok = ok;
            last_health = Instant::now();
        }
        while let Ok(evt) = app.rx.try_recv() {
            match evt {
                UiEvent::User { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.messages.push(Message {
                                speaker: Speaker::User,
                                content: text,
                            });
                        }
                    }
                }
                UiEvent::Tool { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.messages.push(Message {
                                speaker: Speaker::Tool,
                                content: text,
                            });
                        }
                    }
                }
                UiEvent::ToolStep {
                    idx,
                    gen,
                    name,
                    status,
                    summary,
                } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            match status.as_str() {
                                "succeeded" => session.set_busy(BusyReason::WaitingResponse),
                                "failed" => session.set_busy(BusyReason::Error {
                                    message: format!("Tool {} failed", name),
                                }),
                                other => {
                                    session.set_busy(BusyReason::Tool {
                                        name,
                                        status: other.to_string(),
                                        summary,
                                    });
                                }
                            }
                        }
                    }
                }
                UiEvent::ModelToken { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.clear_busy();
                            // Append token to the last model message if present; otherwise start one.
                            if let Some(last) = session.messages.last_mut() {
                                if matches!(last.speaker, Speaker::Model) {
                                    last.content.push_str(&text);
                                } else {
                                    session.messages.push(Message {
                                        speaker: Speaker::Model,
                                        content: text,
                                    });
                                }
                            } else {
                                session.messages.push(Message {
                                    speaker: Speaker::Model,
                                    content: text,
                                });
                            }
                        }
                    }
                }
                UiEvent::ModelOutput { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.clear_busy();
                            // Replace the last model message content with the final text
                            // or create it if it doesn't exist yet.
                            if let Some(last) = session.messages.last_mut() {
                                if matches!(last.speaker, Speaker::Model) {
                                    last.content = text;
                                } else {
                                    session.messages.push(Message {
                                        speaker: Speaker::Model,
                                        content: text,
                                    });
                                }
                            } else {
                                session.messages.push(Message {
                                    speaker: Speaker::Model,
                                    content: text,
                                });
                            }
                        }
                    }
                }
                UiEvent::SetLastId { idx, gen, id } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.last_sse_id = Some(id);
                        }
                    }
                }
                UiEvent::StreamError { idx, gen, message } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.set_busy(BusyReason::Error { message });
                        }
                    }
                }
                UiEvent::StreamClosed { idx, gen } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.clear_busy();
                        }
                    }
                }
            }
        }
        if app.show_agents {
            let refresh_due = app
                .agents_last_fetch
                .map(|last| last.elapsed() >= Duration::from_millis(AGENTS_REFRESH_MS))
                .unwrap_or(true);
            if refresh_due {
                let base_url = app.base_url.clone();
                match fetch_agents(&base_url, &app.http_client).await {
                    Ok(rows) => {
                        app.agents = rows;
                        app.agents_error = None;
                    }
                    Err(err) => {
                        app.agents_error = Some(err);
                    }
                }
                app.agents_last_fetch = Some(Instant::now());
            }
        }
        if app.show_graph {
            let conversation_id = app
                .sessions
                .get(app.active)
                .and_then(|s| s.conversation_id.clone());
            if let Some(conv) = conversation_id {
                let needs_new_conv = app
                    .graph_for_conversation
                    .as_ref()
                    .map(|current| current != &conv)
                    .unwrap_or(true);
                let stale = app
                    .graph_last_fetch
                    .map(|last| last.elapsed() >= Duration::from_millis(GRAPH_REFRESH_MS))
                    .unwrap_or(true);
                if needs_new_conv || stale {
                    let base_url = app.base_url.clone();
                    match fetch_graph(&base_url, &conv, &app.http_client).await {
                        Ok(graph) => {
                            app.graph = Some(graph);
                            app.graph_error = None;
                        }
                        Err(err) => {
                            app.graph_error = Some(err);
                        }
                    }
                    app.graph_for_conversation = Some(conv);
                    app.graph_last_fetch = Some(Instant::now());
                }
            } else {
                app.graph = None;
                app.graph_for_conversation = None;
                if app.graph_error.as_deref()
                    != Some("No conversation yet. Send a message to populate graph.")
                {
                    app.graph_error =
                        Some("No conversation yet. Send a message to populate graph.".to_string());
                }
            }
        }
        terminal.draw(|f| render_ui(f, &mut app))?;
        if event::poll(Duration::from_millis(50))? {
            match event::read()? {
                CEvent::Key(key_event) => {
                    if handle_key_event(key_event, &mut app).await {
                        break;
                    }
                }
                CEvent::Paste(pasted) => {
                    if let Some(session) = app.sessions.get_mut(app.active) {
                        session.input.push_str(&pasted);
                    }
                }
                CEvent::Resize(_, _) => {
                    // Trigger a redraw on next loop iteration (no-op; draw happens each loop)
                }
                _ => {}
            }
        }
    }

    disable_terminal_features()?;
    ratatui::restore();
    Ok(())
}

async fn handle_key_event(key: KeyEvent, app: &mut AppState) -> bool {
    if !app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
        app.focus = FocusTarget::Input;
    }
    match key.code {
        KeyCode::Char(c) => {
            // Handle control combos first
            if key.modifiers.contains(event::KeyModifiers::CONTROL) {
                match c {
                    'l' => {
                        if let Some(session) = app.sessions.get_mut(app.active) {
                            session.messages.clear();
                            session.scroll = 0;
                            session.max_scroll = 0;
                            session.follow = true;
                        }
                        return false;
                    }
                    'u' => {
                        if let Some(session) = app.sessions.get_mut(app.active) {
                            session.input.clear();
                        }
                        return false;
                    }
                    'c' => {
                        app.show_conversations = !app.show_conversations;
                        if app.show_conversations {
                            let list = fetch_conversations(&app.base_url, &app.http_client).await;
                            app.conversations = list;
                            app.conversations_selected = 0;
                            app.focus = FocusTarget::Conversations;
                        } else {
                            app.focus = FocusTarget::Input;
                        }
                        return false;
                    }
                    'r' => {
                        if app.show_conversations {
                            let list = fetch_conversations(&app.base_url, &app.http_client).await;
                            app.conversations = list;
                            if app.conversations_selected >= app.conversations.len() {
                                if app.conversations.is_empty() {
                                    app.conversations_selected = 0;
                                } else {
                                    app.conversations_selected =
                                        app.conversations.len().saturating_sub(1);
                                }
                            }
                        }
                        return false;
                    }
                    'a' => {
                        app.show_agents = !app.show_agents;
                        if app.show_agents {
                            match fetch_agents(&app.base_url, &app.http_client).await {
                                Ok(rows) => {
                                    app.agents = rows;
                                    app.agents_error = None;
                                }
                                Err(err) => {
                                    app.agents_error = Some(err);
                                }
                            }
                            app.agents_last_fetch = Some(Instant::now());
                        }
                        return false;
                    }
                    'g' => {
                        app.show_graph = !app.show_graph;
                        if app.show_graph {
                            app.graph = None;
                            app.graph_error = None;
                            app.graph_for_conversation = None;
                            app.graph_last_fetch = None;
                            let conversation_id = app
                                .sessions
                                .get(app.active)
                                .and_then(|s| s.conversation_id.clone());
                            if let Some(conv) = conversation_id {
                                match fetch_graph(&app.base_url, &conv, &app.http_client).await {
                                    Ok(graph) => {
                                        app.graph = Some(graph);
                                        app.graph_error = None;
                                    }
                                    Err(err) => {
                                        app.graph = None;
                                        app.graph_error = Some(err);
                                    }
                                }
                                app.graph_for_conversation = Some(conv);
                                app.graph_last_fetch = Some(Instant::now());
                            } else {
                                app.graph_error = Some(
                                    "No conversation yet. Send a message to populate graph."
                                        .to_string(),
                                );
                            }
                        } else {
                            app.graph_error = None;
                            app.graph_for_conversation = None;
                            app.graph_last_fetch = None;
                        }
                        return false;
                    }
                    _ => {}
                }
            }

            // Default: append to input
            if !matches!(app.focus, FocusTarget::Input) {
                app.focus = FocusTarget::Input;
            }
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.input.push(c);
            }
        }
        KeyCode::Backspace => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.input.pop();
            }
        }
        KeyCode::Enter => {
            if app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
                // Switch to selected conversation and start SSE
                if let Some(sel_id) = app.conversations.get(app.conversations_selected).cloned() {
                    let idx = app.active;
                    if let Some(session) = app.sessions.get_mut(idx) {
                        session.gen = session.gen.saturating_add(1);
                        let gen = session.gen;
                        if let Some(h) = session.stream_task.take() {
                            h.abort();
                        }
                        session.last_sse_id = None;
                        session.messages.clear();
                        session.conversation_id = Some(sel_id.clone());
                        let handle = spawn_sse_task(
                            app.base_url.clone(),
                            idx,
                            gen,
                            app.tx.clone(),
                            sel_id,
                            None,
                            app.http_client.clone(),
                        );
                        if let Some(s) = app.sessions.get_mut(idx) {
                            s.stream_task = Some(handle);
                        }
                        app.show_conversations = false;
                        app.focus = FocusTarget::Input;
                    }
                }
            } else {
                let idx = app.active;
                if let Some(session) = app.sessions.get_mut(idx) {
                    let input = std::mem::take(&mut session.input);
                    // Increment generation to invalidate any prior stream tasks for this session
                    session.gen = session.gen.saturating_add(1);
                    let gen = session.gen;
                    let tx = app.tx.clone();
                    let base = app.base_url.clone();
                    // Allocate a new conversation id on first send if not set yet
                    if session.conversation_id.is_none() {
                        let ts = SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_millis();
                        session.conversation_id = Some(format!("conv-{}", ts));
                    }
                    let conversation_id = session
                        .conversation_id
                        .clone()
                        .unwrap_or_else(|| format!("conv-{}_fallback", idx + 1));
                    // Abort any previous SSE task for this session to avoid duplicate streams
                    if let Some(handle) = session.stream_task.take() {
                        handle.abort();
                    }
                    // Snapshot last id to resume from the correct position (avoid replaying history)
                    let resume_id = session.last_sse_id.clone();
                    // Capture values needed for async block
                    let http_client = app.http_client.clone();
                    let agent_name = app.agent_name.clone();
                    let tx_clone = app.tx.clone();

                    let send_body = serde_json::json!({
                        "conversation_id": conversation_id,
                        "sender": "user:tui",
                        "recipient": format!("agent:{}", agent_name),
                        "type": "message",
                        "content": input.clone(),
                    });
                    if !app.gateway_ok {
                        session.set_busy(BusyReason::Error {
                            message: "gateway unreachable".to_string(),
                        });
                        let _ = tx.send(UiEvent::Tool {
                            idx,
                            gen,
                            text: "[error] gateway unreachable".to_string(),
                        });
                    } else {
                        session.set_busy(BusyReason::WaitingResponse);

                        // Use unified SSE streaming
                        let handle = tokio::spawn(async move {
                            let client = http_client;
                            match client
                                .post(format!("{}/send", base))
                                .timeout(Duration::from_secs(10))
                                .json(&send_body)
                                .send()
                                .await
                            {
                                Ok(resp) => {
                                    if !resp.status().is_success() {
                                        let status = resp.status();
                                        let text = format!("[error] send failed: {}", status);
                                        let _ = tx_clone.send(UiEvent::Tool {
                                            idx,
                                            gen,
                                            text: text.clone(),
                                        });
                                        let _ = tx_clone.send(UiEvent::StreamError {
                                            idx,
                                            gen,
                                            message: format!("send failed ({})", status),
                                        });
                                        return;
                                    }
                                }
                                Err(_) => {
                                    let _ = tx_clone.send(UiEvent::Tool {
                                        idx,
                                        gen,
                                        text: "[error] send failed".to_string(),
                                    });
                                    let _ = tx_clone.send(UiEvent::StreamError {
                                        idx,
                                        gen,
                                        message: "send failed".to_string(),
                                    });
                                    return;
                                }
                            }
                            // Use unified SSE streaming
                            tokio::spawn(sse::spawn_unified_sse_task(
                                base,
                                conversation_id,
                                resume_id,
                                idx,
                                gen,
                                tx_clone,
                                client,
                            ));
                        });
                        if let Some(session2) = app.sessions.get_mut(idx) {
                            session2.stream_task = Some(handle);
                        }
                    }
                }
            }
        }
        KeyCode::Tab => {
            app.active = (app.active + 1) % app.sessions.len();
            app.focus = FocusTarget::Input;
        }
        KeyCode::BackTab => {
            if app.show_conversations {
                app.focus = match app.focus {
                    FocusTarget::Input => FocusTarget::Conversations,
                    FocusTarget::Conversations => FocusTarget::Input,
                };
            }
        }
        KeyCode::F(2) => {
            let new_idx = app.sessions.len() + 1;
            app.sessions.push(ChatSession {
                title: format!("Chat {}", new_idx),
                messages: Vec::new(),
                input: String::new(),
                gen: 0,
                last_sse_id: None,
                scroll: 0,
                max_scroll: 0,
                viewport_height: 0,
                follow: true,
                stream_task: None,
                conversation_id: None, // new session starts blank; id allocated on first send
                busy: None,
            });
            app.active = app.sessions.len() - 1;
            app.focus = FocusTarget::Input;
        }
        KeyCode::Up => {
            if app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
                if app.conversations_selected > 0 {
                    app.conversations_selected -= 1;
                }
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                if session.scroll > 0 {
                    session.scroll -= 1;
                }
                session.follow = session.scroll == session.max_scroll;
            }
        }
        KeyCode::Down => {
            if app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
                let max = app.conversations.len().saturating_sub(1);
                if app.conversations_selected < max {
                    app.conversations_selected += 1;
                }
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                let new_scroll = session.scroll.saturating_add(1).min(session.max_scroll);
                session.scroll = new_scroll;
                session.follow = session.scroll == session.max_scroll;
            }
        }
        KeyCode::PageUp => {
            if app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
                let dec = app.conversations_selected.saturating_sub(10);
                app.conversations_selected = dec;
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                session.scroll = session.scroll.saturating_sub(10);
                session.follow = session.scroll == session.max_scroll;
            }
        }
        KeyCode::PageDown => {
            if app.show_conversations && matches!(app.focus, FocusTarget::Conversations) {
                let max = app.conversations.len().saturating_sub(1);
                app.conversations_selected = (app.conversations_selected + 10).min(max);
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                let new_scroll = session.scroll.saturating_add(10).min(session.max_scroll);
                session.scroll = new_scroll;
                session.follow = session.scroll == session.max_scroll;
            }
        }
        KeyCode::End => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.scroll = session.max_scroll;
                session.follow = true;
            }
        }
        // Ctrl+L and Ctrl+U handled in the Char(c) branch above
        KeyCode::Esc => {
            return true;
        }
        _ => {}
    }
    false
}

async fn fetch_conversations(base_url: &str, client: &Client) -> Vec<String> {
    let url = format!("{}/conversations", base_url);
    match client
        .get(&url)
        .timeout(Duration::from_secs(6))
        .send()
        .await
    {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(v) => {
                let mut out: Vec<String> = Vec::new();
                if let Some(items) = v.get("conversations").and_then(|x| x.as_array()) {
                    for it in items {
                        if let Some(id) = it.get("id").and_then(|x| x.as_str()) {
                            out.push(id.to_string());
                        }
                    }
                }
                out
            }
            Err(_) => Vec::new(),
        },
        Err(_) => Vec::new(),
    }
}

#[derive(Deserialize)]
struct AgentsEnvelope {
    #[serde(default)]
    agents: Vec<AgentItem>,
}

#[derive(Deserialize)]
struct AgentItem {
    name: String,
    #[serde(default)]
    last_seen_ms: Option<i64>,
    #[serde(default)]
    active_runs: u64,
    #[serde(default)]
    recent_conversations: Vec<String>,
}

async fn fetch_agents(base_url: &str, client: &Client) -> Result<Vec<AgentRow>, String> {
    let url = format!("{}/agents", base_url);
    let resp = client
        .get(&url)
        .timeout(Duration::from_secs(6))
        .send()
        .await
        .map_err(|err| format!("agents request failed: {}", err))?;
    if !resp.status().is_success() {
        return Err(format!("agents endpoint returned {}", resp.status()));
    }
    let envelope = resp
        .json::<AgentsEnvelope>()
        .await
        .map_err(|err| format!("agents payload invalid: {}", err))?;
    let mut out: Vec<AgentRow> = Vec::with_capacity(envelope.agents.len());
    for item in envelope.agents {
        let last_seen = item.last_seen_ms.and_then(|ms| {
            if ms <= 0 {
                None
            } else {
                let ms_u64 = ms as u128;
                let dur = Duration::from_millis((ms_u64).min(u128::from(u64::MAX)) as u64);
                Some(UNIX_EPOCH + dur)
            }
        });
        out.push(AgentRow {
            name: item.name,
            active_runs: item.active_runs,
            last_seen,
            recent_conversations: item.recent_conversations.len(),
        });
    }
    out.sort_by(|a, b| {
        b.active_runs
            .cmp(&a.active_runs)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(out)
}

#[derive(Deserialize)]
struct GraphEnvelope {
    #[serde(default)]
    nodes: Vec<GraphNodeDto>,
    #[serde(default)]
    edges: Vec<GraphEdgeDto>,
}

#[derive(Deserialize)]
struct GraphNodeDto {
    id: String,
    #[serde(rename = "type")]
    kind: String,
}

#[derive(Deserialize)]
struct GraphEdgeDto {
    from: String,
    to: String,
    count: i64,
}

async fn fetch_graph(base_url: &str, conversation_id: &str, client: &Client) -> Result<GraphData, String> {
    let url = format!("{}/graph/{}", base_url, conversation_id);
    let resp = client
        .get(&url)
        .timeout(Duration::from_secs(8))
        .send()
        .await
        .map_err(|err| format!("graph request failed: {}", err))?;
    if resp.status() == StatusCode::NOT_FOUND {
        return Err("graph not available for conversation".to_string());
    }
    if !resp.status().is_success() {
        return Err(format!("graph endpoint returned {}", resp.status()));
    }
    let envelope = resp
        .json::<GraphEnvelope>()
        .await
        .map_err(|err| format!("graph payload invalid: {}", err))?;
    let nodes = envelope
        .nodes
        .into_iter()
        .map(|n| GraphNode {
            id: n.id,
            kind: n.kind,
        })
        .collect();
    let mut edges: Vec<GraphEdge> = Vec::new();
    let mut omitted = 0usize;
    for (idx, edge) in envelope.edges.into_iter().enumerate() {
        if idx < GRAPH_EDGE_LIMIT {
            edges.push(GraphEdge {
                from: edge.from,
                to: edge.to,
                count: edge.count,
            });
        } else {
            omitted += 1;
        }
    }
    Ok(GraphData {
        nodes,
        edges,
        omitted_edges: omitted,
    })
}

fn render_ui(f: &mut Frame, app: &mut AppState) {
    let size = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(3),
        ])
        .split(size);

    let titles: Vec<Line> = app
        .sessions
        .iter()
        .map(|s| Line::from(s.title.as_str()))
        .collect();
    let status = if app.gateway_ok { "ok" } else { "down" };
    let tabs = Tabs::new(titles).select(app.active).block(
        Block::default()
            .borders(Borders::ALL)
            .title(Line::from(format!("Sessions • Gateway: {}", status))),
    );
    f.render_widget(tabs, chunks[0]);

    // Optionally split middle area to show conversations list on the left
    let mut chat_area = chunks[1];
    if app.show_conversations {
        let mid = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Length(28), Constraint::Min(5)])
            .split(chunks[1]);
        let conv_text = if app.conversations.is_empty() {
            "(no conversations)".to_string()
        } else {
            let mut out = String::new();
            for (i, id) in app.conversations.iter().enumerate() {
                if i == app.conversations_selected {
                    out.push_str("> ");
                } else {
                    out.push_str("  ");
                }
                out.push_str(id);
                out.push('\n');
            }
            out
        };
        let mut conv_title =
            "Conversations (Ctrl+C toggle • Ctrl+R refresh • Shift+Tab focus)".to_string();
        if matches!(app.focus, FocusTarget::Conversations) {
            conv_title.push_str(" • [FOCUS]");
        }
        let mut conv_block = Block::default()
            .borders(Borders::ALL)
            .title(Line::from(conv_title));
        if matches!(app.focus, FocusTarget::Conversations) {
            conv_block = conv_block.border_style(
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            );
        }
        let conv = Paragraph::new(conv_text).block(conv_block);
        f.render_widget(conv, mid[0]);
        chat_area = mid[1];
    }

    let mut side_area: Option<Rect> = None;
    if app.show_agents || app.show_graph {
        let split = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Min(40), Constraint::Length(38)])
            .split(chat_area);
        chat_area = split[0];
        side_area = Some(split[1]);
    }

    if let Some(session) = app.sessions.get_mut(app.active) {
        // Styled chat rendering with Markdown-aware content (basic lists/paragraphs)
        let mut lines: Vec<Line> = Vec::with_capacity(session.messages.len() + 1);
        for msg in &session.messages {
            let (label, style) = match msg.speaker {
                Speaker::User => ("You: ", Style::default().fg(Color::Cyan).bold()),
                Speaker::Model => ("AI: ", Style::default().fg(Color::Yellow)),
                Speaker::Tool => ("Tool: ", Style::default().fg(Color::Magenta)),
            };

            let mut opts = MdOptions::empty();
            opts.insert(MdOptions::ENABLE_TABLES);
            opts.insert(MdOptions::ENABLE_FOOTNOTES);
            let parser = MdParser::new_ext(&msg.content, opts);

            let indent = " ".repeat(label.len());
            let mut first_line = true;
            let mut in_item = false;
            let mut current = String::new();

            let push_current = |lines: &mut Vec<Line>,
                                first_line: &mut bool,
                                current: &mut String,
                                in_item: bool| {
                if current.is_empty() {
                    return;
                }
                let mut spans: Vec<Span> = Vec::new();
                if *first_line {
                    spans.push(Span::styled(label, style));
                } else {
                    spans.push(Span::raw(indent.clone()));
                }
                if in_item {
                    spans.push(Span::raw("• "));
                }
                spans.push(Span::raw(current.clone()));
                lines.push(Line::from(spans));
                current.clear();
                *first_line = false;
            };

            for ev in parser {
                match ev {
                    MdEvent::Start(Tag::Item) => {
                        if !current.is_empty() {
                            push_current(&mut lines, &mut first_line, &mut current, in_item);
                        }
                        in_item = true;
                    }
                    MdEvent::End(TagEnd::Item) => {
                        push_current(&mut lines, &mut first_line, &mut current, in_item);
                        in_item = false;
                    }
                    MdEvent::SoftBreak | MdEvent::HardBreak => {
                        push_current(&mut lines, &mut first_line, &mut current, in_item);
                    }
                    MdEvent::Text(t) | MdEvent::Code(t) => {
                        if !current.is_empty() {
                            current.push(' ');
                        }
                        current.push_str(&t);
                    }
                    MdEvent::Start(Tag::Paragraph) | MdEvent::End(TagEnd::Paragraph) => {
                        push_current(&mut lines, &mut first_line, &mut current, in_item);
                    }
                    _ => {}
                }
            }
            if !current.is_empty() {
                push_current(&mut lines, &mut first_line, &mut current, in_item);
            }
        }
        let inner_width = chat_area.width.saturating_sub(2);
        let viewport_height = usize::from(chat_area.height.saturating_sub(2));
        let content_rows = if inner_width == 0 {
            0
        } else {
            Paragraph::new(lines.clone())
                .wrap(Wrap { trim: false })
                .line_count(inner_width)
        };
        let max_scroll = content_rows.saturating_sub(viewport_height);
        let clamped_max = max_scroll.min(u16::MAX as usize) as u16;
        session.viewport_height = viewport_height.min(u16::MAX as usize) as u16;
        session.max_scroll = clamped_max;
        session.scroll = if session.follow {
            session.max_scroll
        } else {
            session.scroll.min(session.max_scroll)
        };

        let mut chat_title = "Chat (PgUp/PgDn/Up/Down to scroll)".to_string();
        if !session.follow {
            chat_title.push_str(" — follow paused (End to resume)");
        }

        let mut paragraph = Paragraph::new(lines)
            .wrap(Wrap { trim: false })
            .scroll((session.scroll, 0));
        paragraph = paragraph.block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(chat_title)),
        );
        f.render_widget(paragraph, chat_area);

        let mut input_title =
            "Input (Enter send, Tab next session, F2 new, Shift+Tab focus panel, Esc quit)"
                .to_string();
        if let Some(busy) = session.busy.as_ref() {
            let elapsed = busy.since.elapsed();
            let suffix = match &busy.reason {
                BusyReason::WaitingResponse => format!(
                    "{} waiting for response • {}",
                    spinner_display(busy.since),
                    format_elapsed_compact(elapsed)
                ),
                BusyReason::Tool {
                    name,
                    status,
                    summary,
                } => {
                    let spinner = spinner_display(busy.since);
                    let mut label = format!("{} tool {} ({})", spinner, name, status);
                    if let Some(s) = summary {
                        let trimmed = s.trim();
                        if !trimmed.is_empty() {
                            let snippet: String = trimmed.chars().take(48).collect();
                            label.push_str(": ");
                            label.push_str(&snippet);
                            if snippet.chars().count() < trimmed.chars().count() {
                                label.push('…');
                            }
                        }
                    }
                    label.push_str(" • ");
                    label.push_str(&format_elapsed_compact(elapsed));
                    label
                }
                BusyReason::Error { message } => {
                    format!("! {}", message)
                }
            };
            input_title.push_str(" • ");
            input_title.push_str(&suffix);
        }
        let mut input_block = Block::default().borders(Borders::ALL).title(Line::from(
            if matches!(app.focus, FocusTarget::Input) {
                let mut title = input_title.clone();
                title.push_str(" • [FOCUS]");
                title
            } else {
                input_title.clone()
            },
        ));
        if matches!(app.focus, FocusTarget::Input) {
            input_block = input_block.border_style(
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            );
        }
        let input = Paragraph::new(session.input.clone()).block(input_block);
        f.render_widget(input, chunks[2]);

        if matches!(app.focus, FocusTarget::Input) {
            let inner_x = chunks[2].x.saturating_add(1);
            let inner_y = chunks[2].y.saturating_add(1);
            let (cursor_col, cursor_row) =
                cursor_position(&session.input, chunks[2].width.saturating_sub(2));
            let cursor_x = inner_x.saturating_add(cursor_col);
            let cursor_y = inner_y.saturating_add(cursor_row);
            if cursor_y < chunks[2].y.saturating_add(chunks[2].height) {
                f.set_cursor_position((cursor_x, cursor_y));
            }
        }
    }

    if let Some(side) = side_area {
        if app.show_agents && app.show_graph {
            let halves = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
                .split(side);
            render_agents_panel(f, halves[0], app);
            render_graph_panel(f, halves[1], app);
        } else if app.show_agents {
            render_agents_panel(f, side, app);
        } else if app.show_graph {
            render_graph_panel(f, side, app);
        }
    }
}

fn cursor_position(input: &str, inner_width: u16) -> (u16, u16) {
    if inner_width == 0 {
        return (0, 0);
    }
    let available = inner_width as usize;
    let last_line = input
        .rsplit_once('\n')
        .map(|(_, tail)| tail)
        .unwrap_or(input);
    let raw_col = last_line.width();
    let capped_col = if available <= 1 { 0 } else { raw_col.min(available - 1) };
    (capped_col.min(u16::MAX as usize) as u16, 0)
}

fn render_agents_panel(f: &mut Frame, area: Rect, app: &AppState) {
    let mut title = format!("Agents ({}) • Ctrl+A toggle", app.agents.len());
    if app.agents_error.is_some() {
        title.push_str(" • last fetch error");
    }
    let mut text = String::new();
    if let Some(err) = &app.agents_error {
        text.push_str(&format!("! {}\n", err));
    }
    if app.agents.is_empty() {
        text.push_str("No agent activity yet.");
    } else {
        let now = SystemTime::now();
        text.push_str("Name             Runs  Last Seen           Recent\n");
        text.push_str("---------------- ----- ------------------- ------\n");
        for agent in &app.agents {
            let name = truncate_with_ellipsis(&agent.name, 16);
            let last_seen = agent
                .last_seen
                .and_then(|ts| now.duration_since(ts).ok())
                .map(|dur| format!("{} ago", format_elapsed_compact(dur)))
                .unwrap_or_else(|| "unknown".to_string());
            text.push_str(&format!(
                "{:<16} {:>4}  {:<19} {:>4}\n",
                name,
                agent.active_runs,
                truncate_with_ellipsis(&last_seen, 19),
                agent.recent_conversations
            ));
        }
    }
    let paragraph = Paragraph::new(text.trim_end().to_string())
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(title)),
        );
    f.render_widget(paragraph, area);
}

fn render_graph_panel(f: &mut Frame, area: Rect, app: &AppState) {
    let mut title = String::from("Conversation Graph • Ctrl+G toggle");
    if let Some(conv) = app.graph_for_conversation.as_deref() {
        title.push_str(" • ");
        title.push_str(&truncate_with_ellipsis(conv, 18));
    }
    let mut text = String::new();
    if let Some(err) = &app.graph_error {
        text.push_str(&format!("! {}\n", err));
    }
    if let Some(graph) = &app.graph {
        if graph.nodes.is_empty() && graph.edges.is_empty() {
            text.push_str("No graph data yet.");
        } else {
            if !graph.nodes.is_empty() {
                text.push_str("Nodes:\n");
                for node in &graph.nodes {
                    text.push_str(&format!(
                        "- {} ({})\n",
                        truncate_with_ellipsis(&node.id, 24),
                        node.kind
                    ));
                }
            }
            if !graph.edges.is_empty() {
                if !text.ends_with('\n') {
                    text.push('\n');
                }
                text.push_str("Edges:\n");
                for edge in &graph.edges {
                    text.push_str(&format!(
                        "- {} -> {} (x{})\n",
                        truncate_with_ellipsis(&edge.from, 16),
                        truncate_with_ellipsis(&edge.to, 16),
                        edge.count
                    ));
                }
                if graph.omitted_edges > 0 {
                    text.push_str(&format!("(+{} more edges omitted)\n", graph.omitted_edges));
                }
            }
        }
    } else if app.graph_error.is_none() {
        text.push_str("Graph data not loaded yet.");
    }
    let paragraph = Paragraph::new(text.trim_end().to_string())
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(title)),
        );
    f.render_widget(paragraph, area);
}

fn truncate_with_ellipsis(input: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    let mut chars = input.chars();
    let actual_len = input.chars().count();
    if actual_len <= max_chars {
        return input.to_string();
    }
    if max_chars <= 3 {
        return ".".repeat(max_chars);
    }
    let take_len = max_chars - 3;
    let mut out = String::with_capacity(max_chars);
    for _ in 0..take_len {
        if let Some(ch) = chars.next() {
            out.push(ch);
        } else {
            break;
        }
    }
    out.push_str("...");
    out
}

fn spawn_sse_task(
    base: String,
    idx: usize,
    gen: u64,
    tx: UnboundedSender<UiEvent>,
    conversation_id: String,
    resume_id: Option<String>,
    client: Client,
) -> JoinHandle<()> {
    tokio::spawn(sse::spawn_unified_sse_task(
        base,
        conversation_id,
        resume_id,
        idx,
        gen,
        tx,
        client,
    ))
}

fn pre_init_terminal() -> io::Result<()> {
    // Best-effort: preserve user scrollback by moving any existing content into history
    if let Ok((_x, y)) = cursor::position() {
        if y > 0 {
            execute!(io::stdout(), ScrollUp(y))?;
        }
        execute!(io::stdout(), cursor::MoveTo(0, 0))?;
        io::stdout().flush().ok();
    }
    Ok(())
}

fn enable_terminal_features() -> io::Result<()> {
    let flags = KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES
        | KeyboardEnhancementFlags::REPORT_EVENT_TYPES
        | KeyboardEnhancementFlags::REPORT_ALTERNATE_KEYS;
    execute!(
        io::stdout(),
        EnableBracketedPaste,
        PushKeyboardEnhancementFlags(flags)
    )?;
    Ok(())
}

fn disable_terminal_features() -> io::Result<()> {
    // Pop enhancement flags and disable bracketed paste; ignore errors
    let _ = execute!(
        io::stdout(),
        PopKeyboardEnhancementFlags,
        DisableBracketedPaste
    );
    Ok(())
}

fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        // Best-effort terminal restore on panic
        let _ = disable_terminal_features();
        ratatui::restore();
        eprintln!("panic: {}", info);
    }));
}

fn discover_base_url() -> String {
    // Try: docker compose port gateway 8000 -> outputs "0.0.0.0:PORT" or ":PORT"
    let out = Command::new("bash")
        .arg("-lc")
        .arg("docker compose port gateway 8000 | head -n1")
        .output();
    if let Ok(o) = out {
        if o.status.success() {
            if let Ok(line) = String::from_utf8(o.stdout) {
                let line = line.trim();
                if let Some((_host, port)) = line.rsplit_once(':') {
                    if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) {
                        return format!("http://localhost:{}", port);
                    }
                }
            }
        }
    }
    "http://localhost:8000".to_string()
}

fn spinner_frame_index(since: Instant) -> usize {
    let elapsed = since.elapsed().as_millis();
    ((elapsed / 160) % SPINNER_FRAMES.len() as u128) as usize
}

fn spinner_display(since: Instant) -> String {
    let frame = SPINNER_FRAMES[spinner_frame_index(since)];
    format!("{:<3}", frame)
}

fn format_elapsed_compact(dur: Duration) -> String {
    if dur.as_secs() >= 3600 {
        let hours = dur.as_secs() / 3600;
        let minutes = (dur.as_secs() % 3600) / 60;
        if minutes == 0 {
            format!("{}h", hours)
        } else {
            format!("{}h{}m", hours, minutes)
        }
    } else if dur.as_secs() >= 60 {
        let minutes = dur.as_secs() / 60;
        let seconds = dur.as_secs() % 60;
        if seconds == 0 {
            format!("{}m", minutes)
        } else {
            format!("{}m{}s", minutes, seconds)
        }
    } else if dur.as_millis() >= 1000 {
        let secs = dur.as_secs();
        let tenths = (dur.subsec_millis() / 100) as u64;
        format!("{}.{:01}s", secs, tenths)
    } else {
        format!("{}ms", dur.as_millis())
    }
}
