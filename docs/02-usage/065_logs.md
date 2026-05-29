# Logs

To see what Serena is doing (especially when something fails):

- **Live stderr** from the MCP subprocess (your client's MCP log view, if available).
- **Persisted files** under:
  - `~/.serena/logs` on Linux and macOS
  - `%USERPROFILE%\.serena\logs` on Windows

Adjust verbosity via `log_level` in [global configuration](050_configuration). Enable `trace_lsp_communication` to log raw language-server protocol messages (mainly for development).
