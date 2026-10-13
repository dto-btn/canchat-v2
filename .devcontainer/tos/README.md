# CANChat Terms Development Container

This Dev Container provides a lightweight local development environment for working on the CANChat Terms of Use pages without installing or running the full CANChat AI backend.

It runs the real Svelte frontend with a small mocked CANChat API and an Ollama stub.

## What it includes

The environment starts three Docker Compose services:

- `workspace` — the VS Code Dev Container and CANChat frontend development environment.
- `canchat-mock` — a lightweight FastAPI backend that provides the API endpoints needed for local Terms of Use development.
- `ollama-stub` — a minimal Ollama-compatible HTTP stub so the application can start without a real model server.

The mock backend is intended for **local development only** and must not be deployed.

## Opening the Dev Container

Open the repository in VS Code and reopen it using:

```text
Dev Containers: Reopen in Container
```

When the container is created, `bootstrap.sh` automatically:

1. Installs frontend dependencies with `npm ci --ignore-scripts`.
2. Creates the local Python virtual environment at `.venv`.
3. Leaves the workspace ready for frontend development.

The mock backend and Ollama stub are started automatically by Docker Compose.

## Start the frontend

Inside the Dev Container terminal, run:

```bash
npm run dev -- --port 5173 --strictPort
```

The CANChat frontend will be available at:

```text
http://localhost:5173
```

For Terms of Use development, open the pages directly:

```text
http://localhost:5173/terms
http://localhost:5173/conditions
```

## Verify the mock backend

From your **host**:

```bash
curl http://localhost:8080/health
```

Expected response:

```json
{"status":"ok","mode":"LOCAL MOCK ONLY"}
```

From **inside the Dev Container**, use the Docker Compose service name instead of `localhost`:

```bash
curl http://canchat-mock:8080/health
```

Inside the workspace container, `localhost:8080` refers to the workspace container itself, not the `canchat-mock` container.

## Ports

| Port | Purpose |
| --- | --- |
| `5173` | Svelte/Vite frontend |
| `8080` | Local FastAPI mock backend |
| `11434` | Ollama stub, available only inside the Compose network |

Ports `5173` and `8080` are bound to `127.0.0.1` on the host.

## Known limitations

This environment intentionally does **not** reproduce the entire CANChat backend.

Some parts of the normal CANChat home page may not work because the mock backend only implements the endpoints needed for Terms of Use development.

### Socket.IO errors

You may see repeated failed requests similar to:

```text
/ws/socket.io/?EIO=4&transport=polling
```

The real CANChat frontend still initializes its Socket.IO client, while the mock backend does not implement Socket.IO.

These errors can be ignored when working on:

```text
/terms
/conditions
```

### Home-page controls

Some controls outside the Terms pages may not respond because their API endpoints are not mocked. For example, release-note or user-settings interactions may call endpoints that are intentionally absent.

## Full backend

A helper script is included for running the real CANChat Python backend:

```bash
bash .devcontainer/terms/start-backend.sh
```

This installs the full backend dependency set from:

```text
backend/requirements.txt
```

The full backend has significantly more dependencies and is **not required** for normal Terms of Use development.

## Rebuilding

If the Dev Container configuration changes, rebuild it from VS Code using:

```text
Dev Containers: Rebuild Container
```

After a normal container restart, you usually only need to restart the frontend:

```bash
npm run dev -- --port 5173 --strictPort
```

## Security

This environment is intentionally insecure and is suitable only for local development.

The mock backend uses:

- a static development token,
- an in-memory terms acceptance store,
- mocked authentication,
- localhost-bound ports.

Do not deploy the mock backend or use it as a production authentication or authorization implementation.