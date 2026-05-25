# iCUE K8s Widget

A Corsair iCUE widget that displays live Kubernetes cluster statistics and warning events on your LCD dashboard, pump LCD, or keyboard LCD. Supports multiple clusters — tap **‹ ›** to cycle through contexts from your kubeconfig.

## Features

- **Cluster stats** — nodes ready/total, running/pending/failed pods, deployment health, namespace count
- **Warning events** — latest non-normal events across all namespaces, sorted by recency
- **Multi-context** — switch between kubeconfig contexts with the on-screen arrows
- **Auto-refresh** — configurable interval (10–120 s) with a live "last refreshed" indicator
- **Themeable** — accent colour, background colour, and transparency via the iCUE property panel

## Architecture

```
iCUE widget (HTML/JS) ──fetch──► Python backend (FastAPI, 127.0.0.1:9090)
                                       │
                                       └──kubeconfig──► K8s cluster(s)
```

The backend reads `~/.kube/config`, exposes a REST API on `127.0.0.1:9090` (localhost only), and handles all cluster auth. The widget talks only to `http://localhost:9090`.

## Prerequisites

| Requirement | Notes |
|---|---|
| [Corsair iCUE](https://www.corsair.com/icue) 5.x+ | Widget host |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 | Runs the backend |
| `kubectl` configured with cluster access | `kubectl get nodes` should work |
| [`just`](https://github.com/casey/just) | Optional — task runner |
| `icue-packager:latest` Docker image | Only for building the `.icuewidget` package; build once from `../icue-widgetbuilder`: `just build-packager` |

## Quick start

```sh
git clone https://github.com/mfic/icue-k8s.git
cd icue-k8s

# Start backend (binds to 127.0.0.1:9090 — localhost only)
docker compose up -d backend

# Verify
curl http://localhost:9090/health
```

Then in iCUE: **Devices → [LCD device] → Widgets → Import widget** and select `dist/k8s.icuewidget`.

## Dev workflow

```sh
just dev         # start widget dev server (http://localhost:8888) + backend
just dev-ui      # widget dev server only
just backend     # backend only
just logs        # stream backend logs
just stop        # stop all services
just package     # build dist/k8s.icuewidget
just install     # package + open in iCUE
just clean       # remove dist/, stop containers
```

Or without `just`:

```sh
docker compose up -d          # full stack (devserver on 8888, backend on 9090)
docker compose up -d backend  # backend only
```

## Widget properties

Configurable from the iCUE settings panel:

| Property | Type | Default | Description |
|---|---|---|---|
| Backend URL | Text | `http://localhost:9090` | URL of the k8s backend |
| Refresh (sec) | Slider 10–120 | `30` | Poll interval |
| Accent Color | Color | `#326ce5` | K8s blue |
| Background | Color | `#07080f` | Background fill |
| Transparency | Slider 0–100 | `100` | Background opacity |

## Switching clusters

- The widget reads all contexts from your kubeconfig on startup
- Tap **‹** / **›** on screen to cycle through contexts
- The selected context persists in `localStorage` across restarts
- First load defaults to your `current-context`

## Backend API

| Endpoint | Description |
|---|---|
| `GET /contexts` | List all kubeconfig contexts |
| `GET /stats?context=<name>` | Node, pod, deployment, namespace counts |
| `GET /events?context=<name>&limit=<1-100>` | Recent Warning/Error events (default 20) |
| `GET /health` | Health check |

Set `DEBUG=true` in the backend environment to enable the Swagger UI at `/docs`.

## Security

The backend binds to `127.0.0.1` and is not reachable from other machines on your network.

> **Do not expose port 9090 publicly.** The backend has read-only access to your kubeconfig and cluster metadata but no authentication beyond the localhost boundary.

The `~/.kube` directory is mounted read-only inside the container.

## Kubeconfig notes

If your kubeconfig references certificates or keys via absolute Windows paths (e.g. from Docker Desktop's built-in Kubernetes), those paths won't resolve inside the container. In that case, convert the relevant entries to embedded base64 credentials (`certificate-authority-data`, `client-certificate-data`, `client-key-data`).

## Contributing

Contributions are welcome. Please open an issue first for non-trivial changes so we can align on approach before you write code.

1. Fork the repo
2. Create a branch: `git checkout -b feat/my-feature`
3. Commit your changes
4. Open a pull request

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) © mfic
