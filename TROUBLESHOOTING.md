# Troubleshooting Guide

This guide provides solutions for specific error messages that are explicitly handled by the agent's codebase.

## `llm_client.py` Errors

These errors originate from the LLM client, which is responsible for making calls to various local and remote language models.

---

### Error: "No OpenAI API key"
- **Cause:** The `OPENAI_API_KEY` environment variable is not set, and it was not found in a `.env` file in the project directory. The system cannot make calls to OpenAI models like GPT-4o without this key.
- **Fix:**
  1.  Create a file named `.env` in the root of the `swarm-memory` directory.
  2.  Add the following line to the file, replacing `sk-xxxxxxxx` with your actual OpenAI API key:
      ```
      OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
      ```
  3.  Alternatively, set the environment variable directly in your shell:
      ```bash
      export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
      ```

---

### Error: "Claude CLI not found" / "Codex CLI not found" / "Copilot CLI not found"
- **Cause:** The agent is configured to use a command-line interface (CLI) tool (`claude`, `codex`, or `copilot`) that is not installed or not available in your system's `PATH`.
- **Fix:**
  1.  Ensure the required CLI tool is installed according to its official documentation.
  2.  Verify that the directory containing the executable is in your system's `PATH`. You can check by running `claude --version`, `codex --version`, or `copilot --version` in your terminal. If the command is not found, you need to add it to your `PATH`.
  3.  If you do not intend to use that provider, edit `llm_client.py` or your configuration to use a different `tier` by default.

---

### Error: "Claude CLI failed: exit <CODE>" / "Codex CLI failed: exit <CODE>" / "Copilot CLI failed: exit <CODE>"
- **Cause:** The specified CLI tool (`claude`, `codex`, `copilot`) was found and executed, but it exited with a non-zero status code, indicating an error. This often happens due to an authentication issue, an invalid model name, or a problem with the service itself.
- **Fix:**
  1.  Run the command manually from your terminal to see the full error message. For example, if a call to Claude failed, try running `claude -p "Hello"` yourself.
  2.  Check your credentials for that specific service. You may need to re-authenticate by running a command like `claude login`.
  3.  Consult the documentation for the specific CLI tool to understand what the exit code means.

---

### Error: "Claude CLI timeout" / "Codex CLI timeout" / "Copilot CLI timeout"
- **Cause:** The call to the external CLI tool took longer than the configured timeout (e.g., 300 seconds). This can happen with very long prompts, slow model responses, or network issues.
- **Fix:**
  1.  Try the prompt again to see if it was a transient issue.
  2.  In `llm_client.py`, find the corresponding `_call_*` function (e.g., `_call_claude`) and increase the `timeout` parameter in the `subprocess.run()` call.

---

### Error: "Unknown tier: <TIER_NAME>"
- **Cause:** The code requested an LLM tier (e.g., `fast`, `code`, `smart`) that is not defined in the `MODELS` dictionary in `llm_client.py`.
- **Fix:**
  1.  Ensure you are using a valid tier name. The available tiers are listed at the top of `llm_client.py`.
  2.  If you intended to create a new tier, add its configuration to the `MODELS` dictionary in `llm_client.py`.

---

### Error: "Unknown provider: <PROVIDER_NAME>"
- **Cause:** The configuration for a tier in `llm_client.py` specifies a `provider` (e.g., `ollama`, `openai`) that does not have a corresponding `_call_*` function to handle it.
- **Fix:**
  1.  Correct the `provider` value in the `MODELS` dictionary to a valid one (`ollama`, `openai`, `claude`, `codex`, `copilot`, `naive`).
  2.  If you are adding a new provider, you must implement a corresponding `_call_<provider>` method within the `LLMClient` class.

---

### Error: "connection_error" (from Ollama)
- **Cause:** The client could not connect to the Ollama server at the configured `OLLAMA_HOST` address. This means the server is not running, is on a different IP/port, or is blocked by a firewall.
- **Fix:**
  1.  Make sure the Ollama server is running on the host machine.
  2.  Verify the `OLLAMA_HOST` environment variable is set correctly. By default, the client tries `http://localhost:11434`.
  3.  Check for firewall rules that might be blocking the connection to the Ollama port.

---

### Error: "timeout" (from Ollama)
- **Cause:** The request to the Ollama server was successful, but the model took too long to generate a response.
- **Fix:**
  1.  In `llm_client.py`, find the `_call_ollama` function and increase the `timeout` parameter.
  2.  Consider using a smaller or faster model for the task.

## `swarm_daemon.py` Errors

These errors occur within the main agent loop as it tries to execute actions.

---

### Error: "Could not acquire lock on <FILE_PATH> within <TIMEOUT>s. Another process may be editing this file."
- **Cause:** The agent tried to edit a file that is currently locked by another process (e.g., another agent instance or a text editor). The locking mechanism is in place to prevent race conditions.
- **Fix:**
  1.  Identify the process that is holding the lock. This is likely another `swarm_daemon.py` process.
  2.  Wait for the other process to finish its operation and release the lock.
  3.  If you are sure no other process is running, you may need to manually delete the `.lock` file (e.g., `my_file.txt.lock`). Do this with caution, as it could lead to data corruption if another process is active.

---

### Error: "Path outside repo root"
- **Cause:** The agent, while running in its default restricted mode, attempted to read or write a file outside of its designated sandbox directory (`repo_root`). This is a security prevention mechanism.
- **Fix:**
  1.  This is expected behavior for security. The agent should only operate on files within its project directory.
  2.  If you trust the agent and its objective completely and need it to access outside files, you must restart it with the `--unrestricted` flag. **Warning: This is dangerous and exposes your system to risk.**

---

### Error: "Command '<COMMAND>' not allowed in reviewed mode"
- **Cause:** The agent attempted to use the `run` action to execute a command that is not on the `SAFE_COMMAND_PREFIXES` allowlist (e.g., `npm`, `python`, `git`). This is a security feature to prevent the execution of arbitrary commands.
- **Fix:**
  1.  If the command is safe and expected, add its prefix to the `SAFE_COMMAND_PREFIXES` tuple at the top of `swarm_daemon.py`.
  2.  If the action is unexpected, this indicates the LLM may be trying to do something unsafe. Review the agent's objective and recent actions.

---

### Error: "exec not allowed in reviewed mode" / "http_request not allowed in reviewed mode"
- **Cause:** The agent tried to use the `exec` or `http_request` actions, which are considered dangerous and are disabled by default.
- **Fix:**
  1.  This is a core security feature. To allow these actions, you must restart the daemon with the `--unrestricted` flag. **Warning: This is dangerous and should only be done in a trusted environment.**

---

### Error: "Command timed out" / "Sub-daemon timeout after <TIMEOUT>s"
- **Cause:** A command executed via the `run` or `exec` action, or a sub-daemon spawned by the agent, took longer to complete than its configured timeout.
- **Fix:**
  1.  Investigate why the command is hanging. Run it manually to debug.
  2.  Increase the `timeout` value in the `subprocess.run()` call within the `run_command` or `execute_action` function in `swarm_daemon.py`.

---

### Error: "LLM response is empty"
- **Cause:** The LLM client returned a successful response, but the text content was empty. This can happen due to API errors, content filtering, or the model simply choosing to output nothing.
- **Fix:**
  1.  Check the `daemon.log` file for the full prompt and any more detailed error messages from the LLM client.
  2.  Try the prompt again. If it persists, the issue may be with the LLM provider or the prompt itself.

## `agent_loop.py` Errors

These errors are specific to the high-level worker/manager loop logic.

---

### Message: "DOOM LOOP: <N> consecutive failures, auto-blocking"
- **Cause:** A specific task has failed `N` times in a row (where `N` is the `DOOM_LOOP_THRESHOLD`, typically 3). The `check_doom_loop` function has detected this pattern and automatically set the task's status to `BLOCKED` to prevent the agent from wasting resources by repeatedly failing on the same task.
- **Fix:**
  1.  This is a feature, not a bug. The agent has stopped itself from getting stuck.
  2.  Manually investigate the task and its history in the `memory.db` database.
  3.  Identify the root cause of the repeated failures. The `RESULT` and `ATTEMPT` glyphs for the blocked `task_id` will contain the error messages.
  4.  Either fix the underlying problem or create a new, more specific TODO for the agent to address the failure. You may need to manually change the task's status from `BLOCKED` back to `OPEN` if you want the agent to retry it after you've made a change.
