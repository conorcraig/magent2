# Plan

## Scope
Replace multiple `Client::new()` instantiations with a single shared HTTP client instance that leverages connection pooling and maintains consistent configuration across all HTTP requests and SSE streams.

## Approach
1. **Create shared client factory** - Use existing `create_http_client()` function
2. **Update function signatures** - Add client parameter to functions that need it
3. **Update all call sites** - Pass client reference from AppState
4. **Remove redundant client creation** - Eliminate `Client::new()` calls
5. **Test functionality** - Ensure all HTTP operations work with shared client

## File Touch List
- `/home/conor/dev/magent2/chat_tui/src/main.rs`
  - Add client parameter to `fetch_graph()` function signature
  - Update all calls to `fetch_conversations()`, `fetch_agents()`, and `fetch_graph()`
  - Replace inline client creation in send task
  - Update health check to use `app.http_client`

- `/home/conor/dev/magent2/chat_tui/src/sse.rs`
  - Accept client parameter in `spawn_unified_sse_task()`
  - Update call to `spawn_unified_sse_task()` in main.rs

## Acceptance Criteria
- [ ] No `Client::new()` calls remain in the codebase (except for client factory)
- [ ] All HTTP requests use the shared client instance
- [ ] SSE streaming continues to work without timeouts
- [ ] Connection pooling is leveraged (evidenced by reduced connection overhead)
- [ ] Health checks use shared client
- [ ] All existing functionality remains intact
- [ ] Tests pass without modification

## Risks & Edge Cases
- **Breaking change**: Function signature changes require updating all call sites
- **Thread safety**: Client must be safe to share across async tasks
- **Timeout behavior**: SSE streams must not have global timeouts
- **Connection limits**: Shared client should handle concurrent requests properly
- **Error handling**: All error paths must continue to work

## Validation Steps
1. **Compile check**: `cargo check` passes without errors
2. **Test execution**: `cargo test` runs successfully
3. **Runtime verification**:
   - Health checks work with shared client
   - Message sending works with shared client
   - SSE streaming works with shared client
   - Agent fetching works with shared client
   - Graph fetching works with shared client
   - Conversation fetching works with shared client
4. **Performance check**: No degradation in response times
5. **Memory check**: No memory leaks from shared client

## Rollback Plan
If issues arise:
1. Revert function signature changes
2. Restore `Client::new()` calls in individual functions
3. Remove client parameter additions
4. Test each function independently

## Implementation Notes
- The shared client is already properly configured in `create_http_client()`
- AppState already has `http_client` field
- Most of the work is updating function signatures and call sites
- The `reqwest::Client` is designed to be shared across threads safely
