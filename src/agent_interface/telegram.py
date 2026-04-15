"""Telegram bot integration for session notifications and replies."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from agent_interface.db import get_connection
from agent_interface.registry import find_session, get_session, list_sessions, list_waiting

CONFIG_PATH = Path.home() / ".config" / "agi" / "config.json"
PIDFILE_PATH = Path.home() / ".config" / "agi" / "bot.pid"
BOT_LOG_PATH = Path.home() / ".config" / "agi" / "bot.log"
DASHBOARD_PATH = Path.home() / ".config" / "agi" / "dashboard.json"
DASHBOARD_THROTTLE_SECONDS = 15


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _api(token: str, method: str, data: dict, timeout: int = 15) -> dict:
    """Call the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError):
        return {"ok": False}


def send_message(text: str, reply_markup: dict | None = None) -> bool:
    """Send a single message to the configured Telegram chat (max 4096 chars)."""
    config = _load_config()
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        return False

    # Telegram limit is 4096 chars — truncate if needed for single messages.
    if len(text) > 4096:
        text = text[:4093] + "…"

    data: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    result = _api(token, "sendMessage", data)
    if result.get("ok"):
        return True

    # HTML parse failed — retry without formatting.
    data["parse_mode"] = ""
    data.pop("reply_markup", None)
    result = _api(token, "sendMessage", data)
    return result.get("ok", False)


def _send_long_message(text: str, reply_markup: dict | None = None) -> bool:
    """Send a message, splitting into multiple if it exceeds 4096 chars.

    The reply_markup (buttons) is attached to the last chunk.
    """
    if len(text) <= 4096:
        return send_message(text, reply_markup=reply_markup)

    # Split on line boundaries.
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > 4000 and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    # Send all chunks. Buttons on the last one.
    ok = True
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        ok = send_message(chunk, reply_markup=reply_markup if is_last else None) and ok

    return ok


def _compact_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        return "~" + cwd[len(home):]
    return cwd


def _format_agent_message(text: str) -> str:
    """Format agent message for Telegram, detecting code blocks."""
    # If the message contains code fences, convert to <pre> tags.
    import re
    # Replace ```lang\n...\n``` with <pre><code>...</code></pre>
    text = re.sub(
        r"```\w*\n(.*?)```",
        r"<pre><code>\1</code></pre>",
        text,
        flags=re.DOTALL,
    )
    # Replace inline `code` with <code>code</code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Replace **bold** with <b>bold</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def notify_waiting(session_id: str, last_message: str | None = None) -> bool:
    """Send a notification that a session is waiting for user input."""
    conn = get_connection()
    s = get_session(conn, session_id)
    if s is None:
        return False

    cwd = _compact_cwd(s.cwd) if s.cwd else "?"
    label = s.label or cwd.rsplit("/", 1)[-1]

    lines = [f"⏳ <b>{label}</b>"]
    lines.append(f"<code>{cwd}</code>")

    # Show what the agent was doing before it stopped.
    if s.last_tool and s.tool_count:
        lines.append(f"📝 {s.tool_count} tool calls · last: {s.last_tool}")

    if last_message:
        formatted = _format_agent_message(last_message)
        lines.append(f"\n<blockquote>{formatted}</blockquote>")

    reply_markup = {
        "inline_keyboard": [[
            {"text": "💬 Reply", "callback_data": f"reply:{session_id}"},
        ]]
    }

    return _send_long_message("\n".join(lines), reply_markup=reply_markup)


# ── pinned dashboard ─────────────────────────────────────────────────────────


def _build_dashboard_text() -> str:
    """Build the dashboard message text from current session state."""
    from agent_interface.registry import is_stale
    from agent_interface.states import SessionState

    conn = get_connection()
    sessions = list_sessions(conn)

    if not sessions:
        return "📊 <b>agi dashboard</b>\n\n<i>No active sessions.</i>"

    state_emoji = {
        "running": "🟢", "waiting_for_user": "🟡",
        "blocked": "🔴", "tests_failed": "🔴",
        "done": "⚫", "idle": "⚪",
    }

    # Group by state priority: waiting first, then running, then other.
    waiting = [s for s in sessions if s.state == SessionState.WAITING_FOR_USER]
    running = [s for s in sessions if s.state == SessionState.RUNNING]
    other = [s for s in sessions if s.state not in (
        SessionState.WAITING_FOR_USER, SessionState.RUNNING,
    )]

    # Build relative timestamp.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    updated = now.strftime("%H:%M UTC")

    lines = [f"📊 <b>agi dashboard</b>  <i>({updated})</i>"]

    # Summary line.
    parts = []
    if waiting:
        parts.append(f"🟡 {len(waiting)} waiting")
    if running:
        parts.append(f"🟢 {len(running)} running")
    if other:
        parts.append(f"⚪ {len(other)} other")
    lines.append(" · ".join(parts))

    def _session_line(s: Any) -> str:
        emoji = state_emoji.get(s.state, "⚪")
        if is_stale(s):
            emoji = "⏸"
        label = s.label or (_compact_cwd(s.cwd).rsplit("/", 1)[-1] if s.cwd else "?")

        # For running: show tool activity.
        if s.state == SessionState.RUNNING and s.last_tool:
            return (
                f"  {emoji} <b>{label}</b>\n"
                f"       <i>{s.last_tool} · {s.tool_count} calls</i>"
            )

        # For waiting: show last agent message.
        if s.state == SessionState.WAITING_FOR_USER:
            snippet = ""
            last = get_last_message_for_session(s.id)
            if last:
                for ln in reversed(last.strip().splitlines()):
                    if ln.strip():
                        snippet = ln.strip()[:80]
                        break
            if snippet:
                return f"  {emoji} <b>{label}</b>\n       <i>{snippet}</i>"

        return f"  {emoji} <b>{label}</b>"

    if waiting:
        lines.append("")
        lines.append("<b>WAITING</b>")
        for s in waiting:
            lines.append(_session_line(s))

    if running:
        lines.append("")
        lines.append("<b>RUNNING</b>")
        for s in running:
            lines.append(_session_line(s))

    if other:
        lines.append("")
        lines.append("<b>OTHER</b>")
        for s in other:
            lines.append(_session_line(s))

    return "\n".join(lines)


def _load_dashboard_state() -> dict:
    if not DASHBOARD_PATH.exists():
        return {}
    try:
        return json.loads(DASHBOARD_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_dashboard_state(state: dict) -> None:
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(json.dumps(state))


def update_dashboard() -> bool:
    """Update the pinned dashboard message. Creates and pins if it doesn't exist.

    Returns True if the dashboard was updated.
    """
    config = _load_config()
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        return False

    # Throttle updates.
    state = _load_dashboard_state()
    now = time.time()
    if now - state.get("last_updated", 0) < DASHBOARD_THROTTLE_SECONDS:
        return False

    text = _build_dashboard_text()
    message_id = state.get("message_id")

    if message_id:
        # Edit existing message.
        result = _api(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        })
        if result.get("ok"):
            state["last_updated"] = now
            _save_dashboard_state(state)
            return True
        # If edit fails (message deleted?), fall through to create new.

    # Create new dashboard message.
    result = _api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    })
    if not result.get("ok"):
        return False

    new_msg_id = result.get("result", {}).get("message_id")
    if new_msg_id:
        # Pin it.
        _api(token, "pinChatMessage", {
            "chat_id": chat_id,
            "message_id": new_msg_id,
            "disable_notification": True,
        })
        state["message_id"] = new_msg_id
        state["last_updated"] = now
        _save_dashboard_state(state)
    return True


def _find_transcript(session_id: str) -> Optional[Path]:
    """Find transcript file for a session by its UUID."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None
    # Search for session_id.jsonl across project dirs.
    for f in claude_dir.rglob(f"{session_id}.jsonl"):
        return f
    return None


def _extract_content(entry: dict) -> str:
    """Extract text content from a transcript entry."""
    msg = entry.get("message", entry)
    content = msg.get("content", "")
    if isinstance(content, list):
        texts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(texts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _read_last_agent_message(transcript_path: str) -> Optional[str]:
    """Read the last assistant text message from a transcript JSONL file.

    Reads from the end for efficiency and to get the most recent message.
    """
    path = Path(transcript_path)
    if not path.exists():
        return None

    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None

    # Walk backwards to find the last assistant message with text.
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = entry.get("role") or entry.get("message", {}).get("role")
        if role == "assistant":
            content = _extract_content(entry)
            if content:
                return content

    return None


def get_last_message_for_session(session_id: str) -> Optional[str]:
    """Get the last assistant message for a session by finding its transcript."""
    path = _find_transcript(session_id)
    if path:
        return _read_last_agent_message(str(path))
    return None


# ── tmux input ───────────────────────────────────────────────────────────────


def _resolve_tmux_target(pid: int) -> Optional[str]:
    """Find the current tmux pane for a PID by walking up its process tree."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_pid} #{session_name}:#{window_index}.#{pane_index}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if out.returncode != 0:
        return None

    # Build pane_pid -> target map.
    pane_map: dict[int, str] = {}
    for line in out.stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            try:
                pane_map[int(parts[0])] = parts[1]
            except ValueError:
                continue

    # Walk up process tree to find a pane.
    visited: set[int] = set()
    current: Optional[int] = pid
    while current and current > 1 and current not in visited:
        visited.add(current)
        if current in pane_map:
            return pane_map[current]
        try:
            from pathlib import Path as _P
            stat = _P(f"/proc/{current}/stat").read_text()
            close_paren = stat.rfind(")")
            fields = stat[close_paren + 2:].split()
            current = int(fields[1])
        except (OSError, IndexError, ValueError):
            break

    return None


def send_to_tmux(tmux_target: str, text: str) -> tuple[bool, str]:
    """Type text into a tmux pane. Returns (success, debug_info)."""
    result = subprocess.run(
        ["tmux", "send-keys", "-t", tmux_target, text, "Enter"],
        capture_output=True, text=True, check=False,
    )
    debug = f"rc={result.returncode} target={tmux_target}"
    if result.stderr:
        debug += f" err={result.stderr.strip()}"
    return result.returncode == 0, debug


def poll_and_reply() -> None:
    """Poll Telegram for messages and route replies to tmux sessions."""
    config = _load_config()
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        return

    offset = 0
    while True:
        result = _api(token, "getUpdates", {"offset": offset, "timeout": 30}, timeout=45)
        if not result.get("ok"):
            time.sleep(5)
            continue

        for update in result.get("result", []):
            offset = update["update_id"] + 1

            # Handle button taps.
            cb = update.get("callback_query")
            if cb:
                _handle_callback(token, cb)
                continue

            msg = update.get("message", {})

            # Only process messages from our chat.
            if msg.get("chat", {}).get("id") != chat_id:
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            # Handle commands.
            if text.startswith("/"):
                _handle_command(token, chat_id, text)
                continue

            # Handle @query message — route to a specific session.
            if text.startswith("@"):
                _handle_at_reply(text)
                continue

            # Plain text — send to active reply target if set.
            target = _get_reply_target()
            if target:
                tmux = _resolve_tmux_target(target["pid"])
                if tmux:
                    ok, _ = send_to_tmux(tmux, text)
                    if ok:
                        from agent_interface.registry import update_state
                        from agent_interface.states import SessionState
                        conn = get_connection()
                        update_state(
                            conn, target["session_id"], SessionState.RUNNING.value,
                        )
                        send_message("✓ Sent.")
                    else:
                        send_message("✗ tmux target not found.")
                else:
                    send_message("✗ Session pane not found.")
            else:
                send_message("Tap 💬 Reply on a notification, or use @query message.")


# ── reply target (set by button tap) ────────────────────────────────────────

_REPLY_TARGET_PATH = Path.home() / ".config" / "agi" / "reply_target.json"


def _set_reply_target(session_id: str, pid: int, label: str) -> None:
    _REPLY_TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPLY_TARGET_PATH.write_text(json.dumps({
        "session_id": session_id,
        "pid": pid,
        "label": label,
        "timestamp": time.time(),
    }))


def _get_reply_target() -> Optional[dict]:
    if not _REPLY_TARGET_PATH.exists():
        return None
    try:
        data = json.loads(_REPLY_TARGET_PATH.read_text())
        # Expire after 10 minutes.
        if time.time() - data.get("timestamp", 0) > 600:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _handle_callback(token: str, cb: dict) -> None:
    """Handle inline keyboard button taps."""
    cb_id = cb.get("id")
    data = cb.get("data", "")

    if data.startswith("reply:"):
        session_id = data[6:]
        conn = get_connection()
        s = get_session(conn, session_id)
        if s:
            _set_reply_target(session_id, s.pid, s.label or "session")
            _api(token, "answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": f"Type your reply for: {s.label or 'session'}",
            })
            send_message(f"💬 Replying to <b>{s.label or session_id[:8]}</b>. Type your message.")
        else:
            _api(token, "answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": "Session not found.",
            })

    elif data.startswith("archive:"):
        session_id = data[8:]
        conn = get_connection()
        s = get_session(conn, session_id)
        if s:
            from agent_interface.registry import archive_session
            archive_session(conn, s.id)
            _api(token, "answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": "Archived.",
            })
            send_message(f"📁 Archived: <b>{s.label or session_id[:8]}</b>")
        else:
            _api(token, "answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": "Session not found.",
            })


def _handle_at_reply(text: str) -> None:
    """Handle @query message — find session and send message in one step.

    Supports: @query message, @"multi word query" message
    """
    without_at = text[1:]  # strip leading @

    # Support quoted query: @"multi word query" message
    if without_at.startswith('"'):
        close = without_at.find('"', 1)
        if close == -1:
            send_message('Missing closing quote. Use @"query" message')
            return
        query = without_at[1:close]
        rest = without_at[close + 1:].strip()
        if not rest:
            send_message('Usage: @"query" your message')
            return
        message = rest
    else:
        parts = without_at.split(None, 1)
        if len(parts) < 2:
            send_message("Usage: @query your message")
            return
        query, message = parts

    conn = get_connection()
    matches = find_session(conn, query)
    if len(matches) == 0:
        send_message(f"No session matching: {query}")
        return
    if len(matches) > 1:
        labels = [f"• {s.label or s.id}" for s in matches[:5]]
        send_message("Ambiguous:\n" + "\n".join(labels))
        return

    s = matches[0]

    # Resolve live tmux pane from PID — don't trust stored metadata.
    tmux = _resolve_tmux_target(s.pid) if s.pid else None
    if not tmux:
        send_message(f"No tmux target for: {s.label or s.id}")
        return
    ok, debug = send_to_tmux(tmux, message)
    if ok:
        # Mark session as running since we just sent input.
        from agent_interface.registry import update_state
        from agent_interface.states import SessionState
        update_state(conn, s.id, SessionState.RUNNING.value)

        label = s.label or s.id
        short = message[:50] + "…" if len(message) > 50 else message
        send_message(f"✓ → <b>{label}</b> (tmux {tmux}): {short}")
    else:
        send_message(f"✗ Failed: {debug}")


def _send_session_cards(sessions: list) -> None:
    """Send a Telegram card for each session with context."""
    state_emoji = {
        "running": "🟢", "waiting_for_user": "🟡",
        "blocked": "🔴", "tests_failed": "🔴",
        "done": "⚫", "idle": "⚪",
    }

    for s in sessions:
        emoji = state_emoji.get(s.state, "⚪")
        label = s.label or (
            _compact_cwd(s.cwd).rsplit("/", 1)[-1] if s.cwd else "session"
        )
        cwd = _compact_cwd(s.cwd) if s.cwd else "?"

        lines = [f"{emoji} <b>{label}</b>"]
        lines.append(f"<code>{cwd}</code>")

        # Tool activity.
        if s.last_tool and s.tool_count:
            lines.append(f"📝 {s.last_tool} · {s.tool_count} calls")

        # Last agent message.
        last = get_last_message_for_session(s.id)
        if last:
            formatted = _format_agent_message(last)
            lines.append(f"\n<blockquote>{formatted}</blockquote>")

        # Reply button for waiting sessions.
        reply_markup = None
        if s.state == "waiting_for_user":
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "💬 Reply", "callback_data": f"reply:{s.id}"},
                ]]
            }

        _send_long_message("\n".join(lines), reply_markup=reply_markup)


def _handle_command(token: str, chat_id: int, text: str) -> None:
    """Handle bot commands."""
    cmd = text.split()[0].lower()

    if cmd == "/list":
        conn = get_connection()
        sessions = list_sessions(conn)
        if not sessions:
            send_message("No active sessions.")
            return
        _send_session_cards(sessions)

    elif cmd == "/waiting":
        conn = get_connection()
        sessions = list_waiting(conn)
        if not sessions:
            send_message("No sessions waiting.")
            return
        _send_session_cards(sessions)

    elif cmd.startswith("/peek"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_message("Usage: /peek session_query")
            return
        conn = get_connection()
        matches = find_session(conn, parts[1])
        if not matches:
            send_message(f"No session matching: {parts[1]}")
        elif len(matches) > 1:
            labels = [f"• {s.label or s.id}" for s in matches[:5]]
            send_message("Ambiguous:\n" + "\n".join(labels))
        else:
            _send_session_cards(matches)

    elif cmd == "/help":
        send_message(
            "<b>Commands:</b>\n"
            "/list — show all active sessions with context\n"
            "/waiting — show sessions needing input\n"
            "/peek &lt;query&gt; — show details for one session\n"
            "/help — this message\n\n"
            "<b>Reply:</b>\n"
            "• Tap 💬 Reply on a notification\n"
            "• Or type @query your message"
        )

    else:
        send_message(f"Unknown command: {cmd}. Try /help")


# ── daemon management ────────────────────────────────────────────────────────


def _bot_pid_alive() -> bool:
    """Check if the bot process from the pidfile is still running."""
    if not PIDFILE_PATH.exists():
        return False
    try:
        pid = int(PIDFILE_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        PIDFILE_PATH.unlink(missing_ok=True)
        return False


def ensure_bot_running() -> None:
    """Start the bot in the background if it's not already running.

    Only starts if Telegram is configured.
    """
    config = _load_config()
    if not config.get("telegram_bot_token") or not config.get("telegram_chat_id"):
        return

    if _bot_pid_alive():
        return

    # Launch bot as a detached subprocess.
    log = open(BOT_LOG_PATH, "a")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_interface.bot_runner"],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )

    PIDFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE_PATH.write_text(str(proc.pid))


def register_commands() -> None:
    """Register bot commands with Telegram for auto-suggest menu."""
    config = _load_config()
    token = config.get("telegram_bot_token")
    if not token:
        return
    _api(token, "setMyCommands", {
        "commands": [
            {"command": "list", "description": "Show all sessions with context"},
            {"command": "waiting", "description": "Show sessions needing input"},
            {"command": "peek", "description": "Peek at a specific session"},
            {"command": "help", "description": "Show help"},
        ]
    })


def stop_bot() -> bool:
    """Stop the background bot process."""
    if not PIDFILE_PATH.exists():
        return False
    try:
        pid = int(PIDFILE_PATH.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        PIDFILE_PATH.unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError, OSError):
        PIDFILE_PATH.unlink(missing_ok=True)
        return False
