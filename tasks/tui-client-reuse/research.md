# Research Notes

## Context
TUI currently creates multiple `reqwest::Client` instances throughout the codebase, which is inefficient and doesn't leverage connection pooling. The issue requires creating a single shared client with proper configuration.

## Current Client Usage Analysis

### Client Creation Points
1. **main.rs:687** - In send task for HTTP POST requests
2. **main.rs:934** - In `fetch_graph()` function for HTTP GET requests
3. **sse.rs:172** - In `spawn_unified_sse_task()` for SSE streaming

### Functions Needing Client Parameter
1. `fetch_conversations()` - Already updated to accept client parameter
2. `fetch_agents()` - Already updated to accept client parameter
3. `fetch_graph()` - Needs client parameter added
4. Send task logic - Needs access to shared client
5. SSE streaming - Needs access to shared client

### Existing Client Configuration
- **create_http_client()** function already exists with:
  - `connect_timeout(Duration::from_secs(10))`
  - `tcp_keepalive(Duration::from_secs(60))`
  - No global timeout (important for SSE)

## Constraints
- Must maintain all existing functionality
- HTTP client should be shared across all requests
- SSE streams must not have global timeouts
- Need to update function signatures to accept client parameter
- Need to update all call sites to pass client reference

## Existing Patterns
- Client is already configured in `create_http_client()` function
- AppState already has access to client via `http_client` field
- Some functions already accept client parameter (fetch_conversations, fetch_agents)

## Assumptions
- All HTTP requests can safely use the same client configuration
- No per-request client customization is needed
- The shared client will be stored in AppState for easy access
- SSE functionality will continue to work with the shared client

## Links/References
- Issue #177: https://github.com/conorcraig/magent2/issues/177
- WHATWG SSE specification for streaming requirements
- reqwest documentation for client configuration options
