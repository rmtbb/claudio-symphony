# Claude Code Trigger Surface: Complete Technical Reference for Ambient Audio

## Vision

An ambient music project where each Claude Code event triggers a tone. A developer with one or more Claude Code terminals hears a continuous, pleasing wash of sounds reflecting every action—tool calls starting/ending, files edited, bash running, subagents spawning, the agent stopping, errors, notifications—creating a sonic feedback layer for development work.

---

## 1. Hook Events: Complete Lifecycle

Claude Code fires hooks at precise lifecycle moments. These are the **primary trigger surface** for ambient audio.

### Session Lifecycle Events

#### SessionStart
- **When**: Session begins (startup, resume, fork, clear)
- **Matchers**: `"startup"`, `"resume"`, `"fork"`, `"clear"`, `"compact"`
- **Frequency**: Once per session
- **JSON Input** (via stdin):
  ```json
  {
    "session_id": "abc123def456",
    "transcript_path": "/Users/user/.claude/projects/project-hash/sessions/session-id.jsonl",
    "cwd": "/current/working/directory",
    "permission_mode": "default|plan|acceptEdits|auto|dontAsk|bypassPermissions",
    "hook_event_name": "SessionStart",
    "source": "startup|resume|fork|clear|compact",
    "model": "claude-opus-4-6",
    "agent_type": "optional-subagent-type"
  }
  ```
- **Audio signal**: Deep tone (session birth), good for rich ambient layer
- **Blocking**: No (runs async by default)
- **Latency**: Can run async; use `async: true` to avoid blocking startup
- **Caveat**: Fires before CLAUDE.md is loaded; cannot reference project files yet

#### SessionEnd
- **When**: Session terminates (user runs `/clear`, quits CLI, context filled)
- **Matchers**: N/A (always matches)
- **Frequency**: Once per session
- **JSON Input**: Same as SessionStart, plus optional `compaction_summary`
- **Audio signal**: Descending tone (session closure), marks end of a session arc
- **Blocking**: No
- **Latency**: Minimal (fires last in session lifecycle)
- **Caveat**: Context may be depleted; transcript is still being written

### Turn Lifecycle Events

#### UserPromptSubmit
- **When**: User submits a prompt
- **Matchers**: N/A
- **Frequency**: Once per prompt
- **JSON Input**:
  ```json
  {
    "hook_event_name": "UserPromptSubmit",
    "prompt": "the full text the user typed",
    "session_id": "...",
    "cwd": "...",
    "permission_mode": "..."
  }
  ```
- **Audio signal**: Bright/ascending tone (user input), marks human intention
- **Control**: Can block (return `decision: "block"`), add context, set session title
- **Latency**: Blocking (hook runs, user waits)

#### Stop
- **When**: Claude finishes a response/action sequence
- **Matchers**: N/A
- **Frequency**: Once per turn
- **JSON Input**: Same common fields
- **Audio signal**: Low tone (task completion), marks Claude's pause point
- **Control**: Can block to continue conversation
- **Blocking**: Yes, controls flow
- **Latency**: ~0 latency before response is shown

#### StopFailure (undocumented but likely)
- **When**: Stop event encounters an error
- **Matchers**: N/A
- **Audio signal**: Dissonant/error tone
- **Blocking**: Yes

### Tool Invocation Events (Per-Tool-Call)

These fire **every time** a tool is invoked. This is the highest-frequency signal.

#### PreToolUse
- **When**: Before ANY tool executes (native or MCP)
- **Matchers**: Tool name (`"Bash"`, `"Edit"`, `"Read"`, `"mcp__server__tool_name"`, or regex)
- **Frequency**: Once per tool call
- **JSON Input**:
  ```json
  {
    "hook_event_name": "PreToolUse",
    "session_id": "...",
    "transcript_path": "...",
    "tool_name": "Bash|Edit|Read|Write|Grep|WebFetch|mcp__memory__insert|etc",
    "tool_input": {
      "command": "npm run build",  // varies by tool
      "path": "/some/file.ts",     // for file tools
      "prompt": "...",             // for LLM tools
      // ... tool-specific fields
    },
    "tool_use_id": "unique-id-for-this-invocation",
    "cwd": "...",
    "permission_mode": "..."
  }
  ```
- **Audio signal**: Bright staccato tone (tool start); pitch can vary by tool type
- **Control** (via `hookSpecificOutput`):
  - `permissionDecision`: `"allow"`, `"deny"`, `"ask"`, `"defer"`
  - `updatedInput`: Modify tool arguments before execution
  - `additionalContext`: Add context for Claude
  - `permissionDecisionReason`: Explanation if denying
- **Blocking**: Yes (permission decision, though can be `"defer"` for async)
- **Latency**: Typically <10ms; tool-specific matchers add negligible overhead
- **Caveat**: 
  - Fires BEFORE permission prompt, so you can control approval
  - MCP tools show up as `tool_name: "mcp__<server>__<tool>"` (underscore-prefixed)
  - Hooks with permission rules (`if: "Bash(rm *)"`) only fire if rule matches

#### PostToolUse
- **When**: After tool succeeds (has output)
- **Matchers**: Tool name
- **Frequency**: Once per successful tool call
- **JSON Input**:
  ```json
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": { ... },
    "tool_output": "stdout/result of the tool",
    "tool_use_id": "...",
    "session_id": "...",
    "cwd": "..."
  }
  ```
- **Audio signal**: Descending tone (tool end); success variant
- **Control**: Can still block, add context (tool already ran)
- **Blocking**: Weak (tool already executed)
- **Latency**: ~0; runs after tool completes

#### PostToolUseFailure
- **When**: After tool fails (has error)
- **Matchers**: Tool name
- **Frequency**: Once per failed tool call
- **JSON Input**: Same as PostToolUse, but has `tool_error` instead of `tool_output`
- **Audio signal**: Dissonant error tone
- **Blocking**: Weak
- **Latency**: ~0

### Permission & User Interaction

#### PermissionRequest
- **When**: Permission dialog is shown to user
- **Matchers**: Tool name, or N/A if global
- **Frequency**: Once per permission prompt
- **JSON Input**:
  ```json
  {
    "hook_event_name": "PermissionRequest",
    "tool_name": "Bash",
    "tool_input": { ... },
    "permission_suggestions": ["allow", "deny", "ask"],
    "session_id": "..."
  }
  ```
- **Audio signal**: High-pitched alert (decision required)
- **Control**: Can auto-allow/deny, modify input
- **Blocking**: Yes (user/hook must decide)

### Subagent Events

#### SubagentStart
- **When**: Subagent spawns
- **Matchers**: Agent type/name (e.g., `"Explore"`, `"Plan"`, or custom agent name)
- **Frequency**: Once per subagent spawn
- **JSON Input**:
  ```json
  {
    "hook_event_name": "SubagentStart",
    "agent_id": "unique-subagent-id",
    "agent_type": "Explore|Plan|general-purpose|custom-name",
    "session_id": "...",
    "transcript_path": "path/to/subagent/transcript.jsonl"
  }
  ```
- **Audio signal**: High, rising tone (worker arrival)
- **Blocking**: No
- **Latency**: Minimal
- **Caveat**: Each subagent has its own session_id and transcript

#### SubagentStop
- **When**: Subagent finishes and returns to parent
- **Matchers**: Agent type
- **Frequency**: Once per subagent lifetime
- **JSON Input**: Same as SubagentStart, plus optional `summary`
- **Audio signal**: Descending tone (worker departure)
- **Blocking**: No
- **Latency**: ~0

### File Change Detection

#### FileChanged
- **When**: Watched file on disk changes (e.g., `.env`, `.envrc`, CLAUDE.md)
- **Matchers**: Literal filenames (no regex unless you escape special chars)
- **Frequency**: Once per file change
- **JSON Input**:
  ```json
  {
    "hook_event_name": "FileChanged",
    "file_path": "/path/to/.env",
    "change_type": "modified|created|deleted",
    "session_id": "..."
  }
  ```
- **Audio signal**: Quiet notification chime (environment change)
- **Blocking**: No
- **Control**: Observability only (cannot prevent change)
- **Caveat**: Only fires for files you explicitly watch in hook config

### System & Setup Events

#### Setup
- **When**: `claude --init-only` or `claude --init` with `--print-only`
- **Matchers**: `"init"`, `"maintenance"`
- **Frequency**: Once per setup run
- **Audio signal**: Ceremonial tone (initialization)

#### InstructionsLoaded
- **When**: CLAUDE.md or rules file loads
- **Matchers**: N/A
- **Frequency**: Multiple times per session (if multiple CLAUDE.md variants load)
- **JSON Input**:
  ```json
  {
    "hook_event_name": "InstructionsLoaded",
    "file_path": "/path/to/CLAUDE.md",
    "memory_type": "personal|project|local|rules",
    "load_reason": "session_start|refresh|update",
    "session_id": "..."
  }
  ```
- **Audio signal**: Subtle underscore (knowledge load)
- **Control**: Observability only

#### Notification
- **When**: Claude Code shows a notification (auth success, permission granted, etc.)
- **Matchers**: Notification type (e.g., `"permission_prompt"`, `"auth_success"`, `"tool_denied"`)
- **Frequency**: Variable
- **JSON Input**:
  ```json
  {
    "hook_event_name": "Notification",
    "notification_type": "permission_prompt|auth_success|tool_denied|...",
    "message": "User-facing message",
    "session_id": "..."
  }
  ```
- **Audio signal**: Short beep (information)
- **Control**: No control, observability

#### UserPromptExpansion (Skill/Command Expansion)
- **When**: User types `/skill-name` and it expands
- **Matchers**: Command/skill name
- **Frequency**: Once per expansion
- **JSON Input**:
  ```json
  {
    "hook_event_name": "UserPromptExpansion",
    "command_name": "skill-name",
    "command_args": "any args",
    "expansion_type": "skill|command",
    "prompt": "rendered prompt after expansion",
    "session_id": "..."
  }
  ```
- **Audio signal**: Ascending tone (command invocation)

---

## 2. Hook Configuration & JSON Structure

Hooks are configured in settings files with a 4-level hierarchy:

```
Managed settings (enterprise) > Local (.claude/settings.local.json) > Project (.claude/settings.json) > User (~/.claude/settings.json)
```

### Configuration Schema

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Read",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/hook.sh",
            "timeout": 5,
            "async": false,
            "asyncRewake": false,
            "shell": "bash"
          }
        ]
      },
      {
        "matcher": "mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/mcp-tools.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/bash-complete.sh",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/turn-complete.sh",
            "async": true
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/session-begin.sh"
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "matcher": "Explore|Plan",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/worker-spawned.sh"
          }
        ]
      }
    ]
  }
}
```

### Hook Handler Types (for Audio)

#### Command Hooks (Recommended for Audio)
```json
{
  "type": "command",
  "command": "/absolute/path/to/script.sh",
  "timeout": 600,
  "async": false,
  "asyncRewake": false,
  "shell": "bash"
}
```
- **Invocation**: Hook receives JSON on stdin, script can output audio commands or IPC messages
- **Exit codes**: 0 (success, parse stdout as JSON), 2 (blocking error), other (non-blocking)
- **Best for ambient**: Yes—script can call `afplay`, `sox`, or post to socket directly
- **Latency**: Blocking by default (set `async: true` to avoid delaying agent)

#### HTTP Hooks (For Remote Audio Daemon)
```json
{
  "type": "http",
  "url": "http://localhost:8080/hooks/sound",
  "timeout": 30,
  "headers": {
    "X-Event-Type": "PreToolUse"
  }
}
```
- **Invocation**: Hook sends JSON in POST body to HTTP endpoint
- **Best for ambient**: Yes—daemon listening on localhost can dispatch audio
- **Latency**: ~1-5ms for localhost, async-friendly

#### MCP Tool Hooks (For Complex Validation)
```json
{
  "type": "mcp_tool",
  "server": "my-sound-server",
  "tool": "play_sound",
  "input": {
    "event": "${hook_event_name}",
    "tool_name": "${tool_name}"
  }
}
```
- **Best for ambient**: Possible but overkill—MCP round-trips are slower

### Matcher Syntax

| Pattern | Type | Example | Matches |
|---------|------|---------|---------|
| `"*"` or empty | Wildcard | Matches all tools/events | Every invocation |
| Simple string | Exact | `"Bash"` | Tool named exactly "Bash" |
| Pipe-separated | OR list | `"Bash\|Read\|Edit"` | Any of Bash, Read, Edit |
| Regex (special chars) | Regex | `"^mcp__"` or `".*memory.*"` | MCP tools, memory tools |

**Examples for audio**:
- `"Bash"` - Match only bash commands
- `"Read\|Write\|Edit"` - Match any file operation
- `"mcp__.*"` - Match all MCP tool calls
- `"*"` - Match every tool invocation (high volume!)
- `"startup\|resume\|fork"` - SessionStart matcher variations

---

## 3. Other Trigger Surfaces Beyond Hooks

### Transcript JSONL File (Real-Time Tail)

Claude Code writes transcripts to:
```
~/.claude/projects/<url-encoded-project-path>/<session-id>.jsonl
```

Or (user-configurable):
```
$CLAUDE_CONFIG_DIR/projects/<url-encoded-project-path>/<session-id>.jsonl
```

**Structure**: One JSON object per line. Each line is a message, tool call, or metadata entry with:
```json
{
  "type": "user_message|assistant_content|tool_use|tool_result|system_message|metadata",
  "id": "uuid",
  "parentUuid": "uuid",
  "timestamp": "2026-05-07T12:34:56Z",
  "content": "text or structured object",
  "tool_use_id": "for tool entries",
  "tool_name": "Bash",
  "tool_input": { ... },
  "tool_output": "...",
  "role": "user|assistant"
}
```

**For ambient audio**:
- **Advantage**: Can tail/watch the file without hook overhead; decoupled from Claude Code
- **Disadvantage**: No execution context (can't know if tool permission was granted, latency measurements harder)
- **Use case**: Low-volume background daemon that watches all sessions' transcripts
- **Example**: `tail -f ~/.claude/projects/*/sessions/*.jsonl | grep -E '"type":"tool_use"' | <parse-and-play-sound>`

### Global History File

Claude Code also maintains:
```
~/.claude/history.jsonl
```

Global index of all prompts across all projects. Less granular but useful for monitoring overall activity.

### Status Line Scripts (Cadence Unknown)

The `statusLine` field in settings.json might run commands on a cadence, but documentation is sparse. Unlikely to be reliable for audio triggers.

### Process Tree Observation

**Not recommended** for ambient audio, but possible:
- Watch the `claude` process tree
- Detect child process spawning (bash, python, node)
- This is extremely fragile and would miss many events

### MCP Tool Invocations

MCP tools are indistinguishable in hooks from native tools:
- Tool name: `"mcp__<server>__<tool>"`
- Appear in PreToolUse/PostToolUse like any other tool
- No special marker beyond naming convention

### Session Lifecycle Files

Location: `~/.claude/projects/<project-id>/` contains:
- Session files with UUIDs as names
- No lock files or session-in-progress markers that are reliable

---

## 4. Granularity of Signal Available per Hook

### PreToolUse

**Available fields to drive audio parameters**:
- `tool_name` (string) → **pitch by tool type** (Bash=low, Edit=mid, Read=high, etc.)
- `tool_input.command` (for Bash) → **can parse intensity** (destructive `rm -rf` vs safe `ls`)
- `tool_input.path` (for file tools) → **can detect file patterns** (.env warning, src/ operation, etc.)
- `session_id` → **identify which terminal** (multi-terminal correlation)
- `cwd` → **context**, which project
- `permission_mode` → **is this auto or manual approval?**

**Example parameter mapping**:
```
tool_name="Bash"        → base frequency 200Hz
tool_input contains "rm" → add dissonance/warning
permission_mode="auto"  → add legato note
→ Result: low, slightly dissonant, smooth tone
```

### PostToolUse / PostToolUseFailure

**Available fields**:
- `tool_name` → **same pitch as PreToolUse**
- `tool_output` (length) → **duration/complexity** (big output = longer decay tail)
- `tool_use_id` → **can pair with PreToolUse** (measure tool latency via session_id + timestamps from transcript)
- `success/failure` → **timbre variant** (success=major, failure=minor)

**Cannot directly extract**: execution time (need to pair Pre/Post via transcript and external timing)

### Stop / SessionEnd

**Available fields**:
- `session_id` → **identify terminal**
- No tool-specific data

**How to infer duration**: Requires external state tracking—hook script reads transcript file and measures time between last PreToolUse and Stop.

### SubagentStart / SubagentStop

**Available fields**:
- `agent_type` → **worker type**, can vary tone (Explore=investigative sound, Plan=thoughtful, etc.)
- `agent_id` → **track subagent lifetime**
- `session_id` → **correlate with parent session**

---

## 5. Multi-Terminal Correlation

If developer has 3 Claude Code terminals open simultaneously:

### Session ID as Unique Identifier

Each session has a **unique `session_id`** (UUID). This appears in every hook input.

**Architecture option 1: Per-session prefix**
```
Hook script receives session_id → prepend to IPC message → Audio daemon maps session_id to audio channel or panning
```
- Session A: left speaker
- Session B: center
- Session C: right speaker
- Result: developer hears 3D spatial ambient music

**Architecture option 2: Shared daemon with session registry**
```
~/.claude/hook-daemon.sock (Unix socket)
Hook script: echo '{"session_id":"abc123","event":"PreToolUse","tool":"Bash"}' → sock
Daemon: reads socket, maintains session→sound-profile map, routes to audio output
```

### CWD as Secondary Identifier

Each hook also provides `cwd` (current working directory). Can use as backup if session_id is unreliable.

### Practical Implementation

**Simplest**: Pass `session_id` to every hook command via environment variable or JSON:
```bash
#!/bin/bash
SESSION_ID=$(jq -r '.session_id' < /dev/stdin)
PROJECT=$(jq -r '.cwd' | sed 's|.*\/||')  # last component
pitch=$(determine_pitch_from_tool_name)

# Play sound routed to daemon with session context
echo "{\"session_id\":\"$SESSION_ID\",\"project\":\"$PROJECT\",\"pitch\":$pitch}" | \
  nc -U ~/.claude/hook-daemon.sock
```

---

## 6. Practical Implementation Patterns

### Architecture: Hook → IPC → Audio Daemon

**Lowest latency on macOS**:

1. **Hook script** (bash/python, <50ms):
   - Parse JSON from stdin
   - Extract event type, tool name, session_id
   - Compute audio parameters
   - Send to daemon via **Unix domain socket** or **FIFO**

2. **IPC mechanism**:
   - **Unix socket** (`~/.claude/hook-daemon.sock`): Bidirectional, reliable, <1ms latency
   - **Named FIFO** (`/tmp/claude-audio.fifo`): Unidirectional, simpler, <1ms latency
   - **HTTP localhost** (`localhost:9999`): Easier debugging, ~2-5ms, overkill for audio

3. **Audio daemon** (background process, continuous):
   - Listen on socket/FIFO
   - Maintain session → sound-profile mapping
   - Queue sounds (avoid polyphonic chaos)
   - Call `afplay` or `sox` to play system sounds or generated tones
   - Optional: layer multiple sounds if events coincide

### Alternative: Direct Audio in Hook

Hook script plays sound directly via `afplay`:

```bash
#!/bin/bash
# extract event type
EVENT=$(jq -r '.hook_event_name')
TOOL=$(jq -r '.tool_name' // 'unknown')

case $TOOL in
  Bash) afplay /System/Library/Sounds/Bell.aiff ;;
  Read) afplay /System/Library/Sounds/Pop.aiff ;;
  Edit) afplay /System/Library/Sounds/Submarine.aiff ;;
esac
```

**Tradeoffs**:
- **Pro**: No daemon needed, hook runs independently
- **Con**: Hook blocks until afplay finishes (set `async: true` to avoid)
- **Con**: No multi-terminal correlation without daemon
- **Con**: Volume/timing not coordinated across multiple tools

### Recommended: Hybrid

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "~/.claude/hooks/ambient-pre.sh",
        "async": true,
        "timeout": 1
      }]
    }]
  }
}
```

Hook script:
```bash
#!/bin/bash
# Read event JSON
EVENT=$(cat)
SESSION=$(echo "$EVENT" | jq -r '.session_id')
TOOL=$(echo "$EVENT" | jq -r '.tool_name')

# Determine sound file based on tool
case $TOOL in
  Bash) SOUND="/System/Library/Sounds/Submarine.aiff" ;;
  Edit) SOUND="/System/Library/Sounds/Pong.aiff" ;;
  Read) SOUND="/System/Library/Sounds/Ping.aiff" ;;
  *) SOUND="/System/Library/Sounds/Pop.aiff" ;;
esac

# Send to daemon (non-blocking)
echo "{\"session\":\"$SESSION\",\"sound\":\"$SOUND\",\"event\":\"$TOOL\"}" | \
  nc -U ~/.claude/hook-daemon.sock 2>/dev/null &

exit 0
```

---

## 7. Gotchas & Caveats

### Hook Timeouts
- Default: 600s for command hooks, 30s for HTTP, 60s for agent
- Set `timeout` to keep hook fast (~1s for audio)
- Timeout = hook cancelled, non-blocking error (agent continues)

### Blocking Behavior
- **PreToolUse**: Blocking; hook decides permission
- **PostToolUse**: Weak blocking (tool already ran)
- **SessionStart/End**: Typically non-blocking (set `async: true` for certain)
- **SubagentStart/Stop**: Non-blocking

### Hook Ordering
- Multiple hooks on same event run sequentially (order not guaranteed)
- If hook 1 blocks (exit code 2), hook 2 may not run

### Permission Prompts
- **PreToolUse fires before permission prompt**
  - Hook can override permission decision
  - User never sees dialog if hook denies/allows
  - Good for automation, bad for transparency

### MCP Tool Naming
- Standard format: `mcp__<server_name>__<tool_name>`
- Example: `mcp__memory__insert_memory`, `mcp__github__search_repos`
- Regex matcher recommended: `"mcp__.*"` to catch all MCP tools

### Session Context Not Available at SessionStart
- Hook fires before CLAUDE.md loads
- Cannot read project config
- Good for setup/initialization, not for tool decisions

### Transcript Tail Limitations
- Transcripts are append-only JSONL
- No sequential ordering guarantee across parallel subagents
- Session_id is reliable; cwd is not (could change mid-session)

### Permission Mode Quirks
- `permission_mode: "auto"` = user pre-approved broad tools
- `permission_mode: "dontAsk"` = permissions strict, may see more PermissionRequest hooks
- Hook can override, but don't trust permission_mode for security

### Latency Sensitivity
- Audio hooks should complete in <100ms
- If hook is slow, agent blocks
- Use `async: true` for background work (statistics, logging, etc.)

### No Hook for Context Compaction
- PreCompact exists (fires before compaction)
- PostCompact does not (compaction modifies context, no post event)
- Cannot trigger sound on compaction completion

---

## 8. Concrete Starter Recipe: Minimal Working Example

### Install Ambient Audio Hook System

#### Step 1: Create hook scripts directory
```bash
mkdir -p ~/.claude/hooks
```

#### Step 2: Hook script for tool events
Save to `~/.claude/hooks/ambient.sh`:

```bash
#!/bin/bash
set -e

# Read JSON from stdin
read -r JSON

# Extract event details
EVENT=$(echo "$JSON" | jq -r '.hook_event_name // "unknown"')
TOOL=$(echo "$JSON" | jq -r '.tool_name // "unknown"')
SESSION=$(echo "$JSON" | jq -r '.session_id' | cut -c1-8)  # short form

# Map tool to sound/pitch
case "$TOOL" in
  Bash)
    # Low, resonant tone for shell commands
    play_sound "Submarine.aiff" "Low"
    ;;
  Edit|Write)
    # Mid-range tone for edits
    play_sound "Pong.aiff" "Mid"
    ;;
  Read|Grep)
    # Higher tone for reads
    play_sound "Ping.aiff" "High"
    ;;
  mcp__*)
    # Bright tone for MCP tools
    play_sound "Pop.aiff" "Bright"
    ;;
  *)
    # Default neutral tone
    play_sound "Pop.aiff" "Neutral"
    ;;
esac

# Helper to play system sounds
play_sound() {
  local sound=$1
  local label=$2
  
  # macOS system sounds
  afplay /System/Library/Sounds/"$sound" 2>/dev/null &
}

# Success (exit 0 = hook succeeded, agent continues)
exit 0
```

Make executable:
```bash
chmod +x ~/.claude/hooks/ambient.sh
```

#### Step 3: Settings file
Create or edit `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/ambient.sh",
            "async": true,
            "timeout": 1
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "afplay /System/Library/Sounds/Glass.aiff 2>/dev/null || true",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "afplay /System/Library/Sounds/Morse.aiff 2>/dev/null || true",
            "async": true,
            "timeout": 1
          }
        ]
      }
    ]
  }
}
```

#### Step 4: Test

Open a Claude Code session:
```bash
cd ~/some-project
claude
```

Type a prompt:
```
Run `npm run build`
```

**Expected**: 
- You hear a deep tone as Claude starts
- You hear a low resonant tone (Submarine) as the bash command runs
- You hear a gentle tone (Morse) when Claude finishes the turn

#### Step 5: Extend to Multiple Sessions

To distinguish terminal A vs B vs C using spatial audio:

```bash
#!/bin/bash
read -r JSON

SESSION=$(echo "$JSON" | jq -r '.session_id' | cut -c1-1 | od -An -td1 | xargs)
PANNING=$((SESSION % 3))  # 0 = left, 1 = center, 2 = right

# (macOS doesn't have built-in panning; would need sox or separate speakers)
# For now, use different sounds per session
case "$PANNING" in
  0) afplay /System/Library/Sounds/Submarine.aiff ;;
  1) afplay /System/Library/Sounds/Pop.aiff ;;
  2) afplay /System/Library/Sounds/Ping.aiff ;;
esac

exit 0
```

---

## Summary: Trigger Surface at a Glance

| **Event** | **Frequency** | **Blocking** | **Rich Data** | **Best for Audio** |
|-----------|---------------|--------------|---------------|--------------------|
| PreToolUse | Per tool call | Yes | tool_name, input | Primary signal |
| PostToolUse | Per tool call | Weak | tool_name, output | Completion note |
| SessionStart | Per session | No | session_id, source | Opening chord |
| SessionEnd | Per session | No | session_id | Closing chord |
| Stop | Per turn | Yes | minimal | Pause marker |
| SubagentStart | Per subagent | No | agent_type | Arrival fanfare |
| SubagentStop | Per subagent | No | agent_type | Departure tone |
| PermissionRequest | Per prompt | Yes | tool_name | Alert chime |
| UserPromptSubmit | Per prompt | Yes | prompt text | User input note |
| FileChanged | Per file change | No | file_path, change_type | Environment shift |

**Lowest-latency architecture**: Hook script (bash) → `afplay` directly, with `async: true` to avoid blocking. Scales to ~50 PreToolUse events per second without audio stalling.

**Recommended for multi-terminal**: Hook script → Unix socket → daemon (Python/Node) that maintains session-to-sound mapping and queues overlapping events.

