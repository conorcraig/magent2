use std::io::{self, Write};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use std::process::Command;

use crossterm::event::{
    self,
    Event as CEvent,
    KeyCode,
    KeyEvent,
    DisableBracketedPaste,
    EnableBracketedPaste,
    KeyboardEnhancementFlags,
    PopKeyboardEnhancementFlags,
    PushKeyboardEnhancementFlags,
};
use crossterm::{cursor, execute};
use crossterm::terminal::ScrollUp;
use ratatui::prelude::*;
use ratatui::widgets::{Block, Borders, Paragraph, Tabs, Wrap};
use ratatui::text::Line;
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio::task::JoinHandle;
use reqwest::Client;
use futures_util::StreamExt;
use serde_json;

// UI event bus carrying structured events to the render loop.
enum UiEvent {
    User { idx: usize, gen: u64, text: String },
    ModelToken { idx: usize, gen: u64, text: String },
    ModelOutput { idx: usize, gen: u64, text: String },
    Tool { idx: usize, gen: u64, text: String },
    SetLastId { idx: usize, gen: u64, id: String },
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

struct ChatSession {
    title: String,
    messages: Vec<Message>,
    input: String,
    gen: u64, // increments each time user sends; filters stale stream tasks
    last_sse_id: Option<String>,
    scroll: u16,
    stream_task: Option<JoinHandle<()>>, // abort previous SSE task on new send
    conversation_id: Option<String>, // None until first send, then set to new id
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
}

impl AppState {
    fn new() -> Self {
        let (tx, rx) = mpsc::unbounded_channel();
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
                stream_task: None,
                conversation_id: None,
            }],
            active: 0,
            rx,
            tx,
            base_url,
            gateway_ok: false,
            agent_name: std::env::var("MAGENT2_AGENT_NAME").unwrap_or_else(|_| "DevAgent".to_string()),
            show_conversations: false,
            conversations: Vec::new(),
            conversations_selected: 0,
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
    let client = Client::new();
    let mut last_health = Instant::now() - Duration::from_secs(2);

    loop {
        // Periodic gateway health probe (best-effort)
        if last_health.elapsed() >= Duration::from_millis(750) {
            let url = format!("{}/health", app.base_url);
            let ok = match client.get(&url).timeout(Duration::from_millis(1200)).send().await {
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
                            session.messages.push(Message { speaker: Speaker::User, content: text });
                        }
                    }
                }
                UiEvent::Tool { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            session.messages.push(Message { speaker: Speaker::Tool, content: text });
                        }
                    }
                }
                UiEvent::ModelToken { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            // Append token to the last model message if present; otherwise start one.
                            if let Some(last) = session.messages.last_mut() {
                                if matches!(last.speaker, Speaker::Model) {
                                    last.content.push_str(&text);
                                } else {
                                    session.messages.push(Message { speaker: Speaker::Model, content: text });
                                }
                            } else {
                                session.messages.push(Message { speaker: Speaker::Model, content: text });
                            }
                        }
                    }
                }
                UiEvent::ModelOutput { idx, gen, text } => {
                    if let Some(session) = app.sessions.get_mut(idx) {
                        if gen == session.gen {
                            // Replace the last model message content with the final text
                            // or create it if it doesn't exist yet.
                            if let Some(last) = session.messages.last_mut() {
                                if matches!(last.speaker, Speaker::Model) {
                                    last.content = text;
                                } else {
                                    session.messages.push(Message { speaker: Speaker::Model, content: text });
                                }
                            } else {
                                session.messages.push(Message { speaker: Speaker::Model, content: text });
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
            }
        }
        terminal.draw(|f| render_ui(f, &app))?;
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
    match key.code {
        KeyCode::Char(c) => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.input.push(c);
            }
        }
        KeyCode::Char('c') => {
            app.show_conversations = !app.show_conversations;
            if app.show_conversations {
                let list = fetch_conversations(&app.base_url).await;
                app.conversations = list;
                app.conversations_selected = 0;
            }
        }
        KeyCode::Char('r') => {
            if app.show_conversations {
                let list = fetch_conversations(&app.base_url).await;
                app.conversations = list;
                if app.conversations_selected >= app.conversations.len() {
                    if app.conversations.is_empty() { app.conversations_selected = 0; }
                    else { app.conversations_selected = app.conversations.len().saturating_sub(1); }
                }
            }
        }
        KeyCode::Backspace => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.input.pop();
            }
        }
        KeyCode::Enter => {
            if app.show_conversations {
                // Switch to selected conversation and start SSE
                if let Some(sel_id) = app.conversations.get(app.conversations_selected).cloned() {
                    let idx = app.active;
                    if let Some(session) = app.sessions.get_mut(idx) {
                        session.gen = session.gen.saturating_add(1);
                        let gen = session.gen;
                        if let Some(h) = session.stream_task.take() { h.abort(); }
                        session.last_sse_id = None;
                        session.messages.clear();
                        session.conversation_id = Some(sel_id.clone());
                        let handle = spawn_sse_task(
                            app.base_url.clone(), idx, gen, app.tx.clone(), sel_id, None,
                        );
                        if let Some(s) = app.sessions.get_mut(idx) { s.stream_task = Some(handle); }
                        app.show_conversations = false;
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
                    let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis();
                    session.conversation_id = Some(format!("conv-{}", ts));
                }
                let conversation_id = session.conversation_id.clone().unwrap_or_else(|| format!("conv-{}_fallback", idx + 1));
                // Abort any previous SSE task for this session to avoid duplicate streams
                if let Some(handle) = session.stream_task.take() {
                    handle.abort();
                }
                // Snapshot last id to resume from the correct position (avoid replaying history)
                let resume_id = session.last_sse_id.clone();
                let send_body = serde_json::json!({
                    "conversation_id": conversation_id,
                    "sender": "user:tui",
                    "recipient": format!("agent:{}", app.agent_name),
                    "type": "message",
                    "content": input.clone(),
                });
                if !app.gateway_ok {
                    let _ = tx.send(UiEvent::Tool { idx, gen, text: "[error] gateway unreachable".to_string() });
                } else {
                let handle = tokio::spawn(async move {
                    let client = Client::new();
                    match client.post(format!("{}/send", base)).json(&send_body).send().await {
                        Ok(resp) => {
                            if !resp.status().is_success() {
                                let _ = tx.send(UiEvent::Tool { idx, gen, text: format!("[error] send failed: {}", resp.status()) });
                                return;
                            }
                        }
                        Err(_) => {
                            let _ = tx.send(UiEvent::Tool { idx, gen, text: "[error] send failed".to_string() });
                            return;
                        }
                    }
                    // Stream SSE line-by-line; handle data: JSON payloads
                    let url = format!("{}/stream/{}", base, conversation_id);
                    let mut req = client.get(&url);
                    if let Some(id) = resume_id {
                        if !id.is_empty() {
                            req = req.header("Last-Event-ID", id);
                        }
                    }
                    if let Ok(resp) = req.send().await {
                        if resp.status().is_success() {
                            let mut bytes_stream = resp.bytes_stream();
                            let mut buf: Vec<u8> = Vec::new();
                            while let Some(item) = bytes_stream.next().await {
                                match item {
                                    Ok(chunk) => {
                                        for b in chunk {
                                            if b == b'\n' {
                                                if let Ok(line) = String::from_utf8(buf.clone()) {
                                                    let s = line.trim_end_matches('\r');
                                                    if s.starts_with("id: ") {
                                                        let id = s[4..].trim().to_string();
                                                        let _ = tx.send(UiEvent::SetLastId { idx, gen, id });
                                                    } else {
                                                        handle_sse_line(&line, idx, gen, &tx);
                                                    }
                                                }
                                                buf.clear();
                                            } else {
                                                buf.push(b);
                                            }
                                        }
                                    }
                                    Err(_) => break,
                                }
                            }
                        }
                    }
                });
                // Store handle so we can abort next time
                if let Some(session2) = app.sessions.get_mut(idx) { session2.stream_task = Some(handle); }
                }
            }
        }
        KeyCode::Tab => {
            app.active = (app.active + 1) % app.sessions.len();
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
                stream_task: None,
                conversation_id: None, // new session starts blank; id allocated on first send
            });
            app.active = app.sessions.len() - 1;
        }
        KeyCode::Up => {
            if app.show_conversations {
                if app.conversations_selected > 0 { app.conversations_selected -= 1; }
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                if session.scroll > 0 { session.scroll -= 1; }
            }
        }
        KeyCode::Down => {
            if app.show_conversations {
                let max = app.conversations.len().saturating_sub(1);
                if app.conversations_selected < max { app.conversations_selected += 1; }
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                // naive increment; rendering will clamp visually
                session.scroll = session.scroll.saturating_add(1);
            }
        }
        KeyCode::PageUp => {
            if app.show_conversations {
                let dec = app.conversations_selected.saturating_sub(10);
                app.conversations_selected = dec;
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                session.scroll = session.scroll.saturating_sub(10);
            }
        }
        KeyCode::PageDown => {
            if app.show_conversations {
                let max = app.conversations.len().saturating_sub(1);
                app.conversations_selected = (app.conversations_selected + 10).min(max);
            } else if let Some(session) = app.sessions.get_mut(app.active) {
                session.scroll = session.scroll.saturating_add(10);
            }
        }
        KeyCode::Char('l') if key.modifiers.contains(event::KeyModifiers::CONTROL) => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.messages.clear();
                session.scroll = 0;
            }
        }
        KeyCode::Char('u') if key.modifiers.contains(event::KeyModifiers::CONTROL) => {
            if let Some(session) = app.sessions.get_mut(app.active) {
                session.input.clear();
            }
        }
        KeyCode::Esc => {
            return true;
        }
        _ => {}
    }
    false
}

async fn fetch_conversations(base_url: &str) -> Vec<String> {
    let url = format!("{}/conversations", base_url);
    let client = Client::new();
    match client.get(&url).send().await {
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

fn render_ui(f: &mut Frame, app: &AppState) {
    let size = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(5), Constraint::Length(3)])
        .split(size);

    let titles: Vec<Line> = app.sessions.iter().map(|s| Line::from(s.title.as_str())).collect();
    let status = if app.gateway_ok { "ok" } else { "down" };
    let tabs = Tabs::new(titles)
        .select(app.active)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(format!("Sessions • Gateway: {}", status))),
        );
    f.render_widget(tabs, chunks[0]);

    // Optionally split middle area to show conversations list on the left
    let chat_area = if app.show_conversations {
        let mid = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Length(28), Constraint::Min(5)])
            .split(chunks[1]);
        let conv_text = if app.conversations.is_empty() { "(no conversations)".to_string() } else {
            let mut out = String::new();
            for (i, id) in app.conversations.iter().enumerate() {
                if i == app.conversations_selected { out.push_str("> "); } else { out.push_str("  "); }
                out.push_str(id);
                out.push('\n');
            }
            out
        };
        let conv = Paragraph::new(conv_text)
            .block(Block::default().borders(Borders::ALL).title("Conversations (c to toggle)"));
        f.render_widget(conv, mid[0]);
        mid[1]
    } else {
        chunks[1]
    };

    if let Some(session) = app.sessions.get(app.active) {
        // Styled chat rendering
        let mut lines: Vec<Line> = Vec::with_capacity(session.messages.len() + 1);
        for msg in &session.messages {
            let (label, style) = match msg.speaker {
                Speaker::User => ("You: ", Style::default().fg(Color::Cyan).bold()),
                Speaker::Model => ("AI: ", Style::default().fg(Color::Yellow)),
                Speaker::Tool => ("Tool: ", Style::default().fg(Color::Magenta)),
            };
            let mut spans: Vec<Span> = Vec::with_capacity(2);
            spans.push(Span::styled(label, style));
            spans.push(Span::raw(&msg.content));
            lines.push(Line::from(spans));
        }
        let paragraph = Paragraph::new(lines)
            .wrap(Wrap { trim: false })
            .scroll((session.scroll, 0))
            .block(Block::default().borders(Borders::ALL).title("Chat (PgUp/PgDn/Up/Down to scroll)"));
        f.render_widget(paragraph, chat_area);

        let input = Paragraph::new(session.input.clone()).block(Block::default().borders(Borders::ALL).title("Input (Enter to send, Tab switch, F2 new, Esc quit)"));
        f.render_widget(input, chunks[2]);
    }
}

fn spawn_sse_task(
    base: String,
    idx: usize,
    gen: u64,
    tx: UnboundedSender<UiEvent>,
    conversation_id: String,
    resume_id: Option<String>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let client = Client::new();
        let url = format!("{}/stream/{}", base, conversation_id);
        let mut req = client.get(&url);
        if let Some(id) = resume_id { if !id.is_empty() { req = req.header("Last-Event-ID", id); } }
        if let Ok(resp) = req.send().await {
            if resp.status().is_success() {
                let mut bytes_stream = resp.bytes_stream();
                let mut buf: Vec<u8> = Vec::new();
                while let Some(item) = bytes_stream.next().await {
                    match item {
                        Ok(chunk) => {
                            for b in chunk {
                                if b == b'\n' {
                                    if let Ok(line) = String::from_utf8(buf.clone()) {
                                        let s = line.trim_end_matches('\r');
                                        if s.starts_with("id: ") {
                                            let id = s[4..].trim().to_string();
                                            let _ = tx.send(UiEvent::SetLastId { idx, gen, id });
                                        } else {
                                            handle_sse_line(&line, idx, gen, &tx);
                                        }
                                    }
                                    buf.clear();
                                } else { buf.push(b); }
                            }
                        }
                        Err(_) => break,
                    }
                }
            }
        }
    })
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
    execute!(io::stdout(), EnableBracketedPaste, PushKeyboardEnhancementFlags(flags))?;
    Ok(())
}

fn disable_terminal_features() -> io::Result<()> {
    // Pop enhancement flags and disable bracketed paste; ignore errors
    let _ = execute!(io::stdout(), PopKeyboardEnhancementFlags, DisableBracketedPaste);
    Ok(())
}

fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        // Best-effort terminal restore on panic
        let _ = disable_terminal_features();
        let _ = ratatui::restore();
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

// Parse one SSE line and forward interesting events to the UI channel
fn handle_sse_line(line: &str, idx: usize, gen: u64, tx: &UnboundedSender<UiEvent>) {
    let s = line.trim_end_matches('\r');
    if s.is_empty() || s.starts_with(':') {
        return;
    }
    if let Some(data) = s.strip_prefix("data: ") {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(data) {
            let ev = v.get("event").and_then(|x| x.as_str()).unwrap_or("");
            match ev {
                "token" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::ModelToken { idx, gen, text: text.to_string() });
                    }
                }
                "output" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::ModelOutput { idx, gen, text: text.to_string() });
                    }
                }
                "tool_step" => {
                    let name = v.get("name").and_then(|x| x.as_str()).unwrap_or("tool");
                    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
                    let summary = v.get("result_summary").and_then(|x| x.as_str()).unwrap_or("");
                    let line = if summary.is_empty() {
                        format!("[tool:{}] {}", status, name)
                    } else {
                        format!("[tool:{}] {}: {}", status, name, summary)
                    };
                    let _ = tx.send(UiEvent::Tool { idx, gen, text: line });
                }
                "user_message" => {
                    if let Some(text) = v.get("text").and_then(|t| t.as_str()) {
                        let _ = tx.send(UiEvent::User { idx, gen, text: text.to_string() });
                    }
                }
                _ => {}
            }
        }
    }
}
