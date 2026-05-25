# icue-k8s

iCUE widget displaying Kubernetes cluster statistics and error events on the XENEON EDGE. Supports multiple clusters — tap the `‹ ›` arrows on the screen to switch between contexts from your kubeconfig.

## Architecture

```
iCUE widget (HTML/JS) ──fetch──► Python backend (FastAPI)
                                       │
                                       └──kubeconfig──► K8s cluster(s)
```

The backend reads `~/.kube/config`, exposes a REST API on `:9090`, and handles all cluster auth. The widget talks only to `http://localhost:9090`.

## Prerequisites

- WSL2 + Docker Desktop (WSL2 integration enabled)
- `just`
- `kubectl` configured with cluster access (`kubectl get nodes` works)
- `icue-packager:latest` Docker image — build once from `../icue-widgetbuilder`:
  ```sh
  cd ../icue-widgetbuilder && just build-packager
  ```

## Dev workflow

```sh
just dev         # start widget dev server (http://localhost:8888) + backend (http://localhost:9090)
just dev-ui      # start only the widget dev server
just backend     # start only the backend
just logs        # stream backend logs
just stop        # stop all services
just package     # build dist/k8s.icuewidget
just install     # package + open in iCUE
just clean       # remove dist/, stop containers
```

## Widget properties

Configurable from the iCUE settings panel:

| Property | Type | Default | Description |
|---|---|---|---|
| Backend URL | Text | `http://localhost:9090` | URL of the k8s backend |
| Refresh (sec) | Slider 10–120 | 30 | How often to poll the cluster |
| Accent Color | Color | `#326ce5` | K8s blue |
| Background | Color | `#07080f` | Background fill |
| Transparency | Slider 0–100 | 100 | Background opacity |

## Switching clusters

- The widget reads all contexts from your kubeconfig on startup
- Tap **‹** / **›** on the screen to cycle through contexts
- The selected context is stored in localStorage and persists across restarts
- The first load defaults to your `current-context`

## Backend API

| Endpoint | Description |
|---|---|
| `GET /contexts` | List all kubeconfig contexts |
| `GET /stats?context=<name>` | Node, pod, deployment, namespace counts |
| `GET /events?context=<name>` | Recent Warning/Error events (last 15) |
| `GET /health` | Backend health check |

## Kubeconfig notes

The backend mounts `~/.kube` read-only inside Docker. If your kubeconfig references certificates or keys via absolute Windows paths (e.g. from Docker Desktop's built-in k8s), those paths won't be accessible from the container. In that case, copy or convert the relevant entries to embedded base64 credentials (`certificate-authority-data`, `client-certificate-data`, `client-key-data`).
