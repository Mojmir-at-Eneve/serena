# Docker Setup for Serena (Experimental)

⚠️ **EXPERIMENTAL FEATURE**: The Docker setup for Serena is still experimental and has some limitations. Read this document before using Docker with Serena.

## Overview

Docker support lets you run Serena in an isolated container, which provides better security isolation for shell execution and consistent dependencies across systems.

## Benefits

- **Safer shell tool execution**: Commands run in an isolated container environment
- **Consistent dependencies**: No need to manage language servers and dependencies on your host system
- **Cross-platform support**: Works consistently across Windows, macOS, and Linux

## Important usage pointers

### Configuration

Serena's configuration and log files are stored in the container under `/workspaces/serena/config/`.
Host `~/.serena` settings are not used unless you mount them.

Mount a local directory to `/workspaces/serena/config` to persist settings across restarts (including logs).
Add a `serena_config.yml` there with any overrides you need (see [configuration docs](docs/02-usage/050_configuration.md)).

### Project activation limitations

- **Only mounted directories work**: Projects must be mounted as volumes
- Projects outside mounted paths cannot be activated
- Use full container paths when activating (e.g. `/workspaces/projects/my-project`)

### Language support limitations

The default image may not include system-level dependencies for every language.
Languages that download their own tooling on first use work best out of the box.

### Line ending issues on Windows

Files edited in the container may use LF line endings. Configure Git appropriately: `git config core.autocrlf true`.

## Quick start

### Using Docker Compose (recommended)

1. **Production mode** (MCP server):

   ```bash
   docker-compose up serena
   ```

2. **Development mode** (source mounted):

   ```bash
   docker-compose up serena-dev
   ```

Edit `compose.yaml` to mount your project directories.

### Building manually

```bash
docker build -t serena .

docker run -it --rm \
  -v "$(pwd)":/workspace \
  -p 9121:9121 \
  -e SERENA_DOCKER=1 \
  serena
```

### Compose override example

```yaml
services:
  serena:
    volumes:
      - ./my-project:/workspace/my-project
    command:
      - "uv run --directory . serena start-mcp-server --transport sse --port 9121 --host 0.0.0.0 --project /workspace/my-project"
```

## Volume mounting

```yaml
volumes:
  - ./my-project:/workspace/my-project
  - /path/to/another/project:/workspace/another-project
```

## Environment variables

- `SERENA_DOCKER=1`: Set automatically in the provided compose files
- `SERENA_PORT`: MCP server port (default: 9121)
- `INTELEPHENSE_LICENSE_KEY`: Optional PHP LSP license key

## Troubleshooting

### Port already in use

```bash
lsof -i :9121   # macOS/Linux
SERENA_PORT=9122 docker-compose up serena
```

### Project access issues

- Check volume mounts in `compose.yaml`
- Use absolute paths for external projects
- Verify permissions on mounted directories
