# iCUE K8s Widget

A Corsair iCUE widget that displays live Kubernetes cluster statistics on your LCD dashboard, pump LCD, or keyboard LCD. Supports multiple clusters — tap **‹ ›** to cycle through contexts from your kubeconfig.

## Features

- **Overview tab** — nodes ready/total, running/pending/failed pods, deployment health, namespace count, and latest warning events
- **Workloads tab** — all namespaces listed with pod/deployment counts; tap any namespace to drill into its Deployments, StatefulSets, and DaemonSets
- **Health tab** — per-node CPU/memory (split by master/worker), active pod issues, PVC health, and TLS certificate expiry
- **Multi-context** — switch between kubeconfig contexts with the on-screen arrows
- **Auto-refresh** — configurable interval (10–120 s) with a live "last refreshed" indicator
- **Themeable** — accent colour, background colour, and transparency via the iCUE property panel

## Screenshots

> _Three-tab navigation: Overview · Workloads · Health_

## Architecture

```
iCUE widget (HTML/JS) ──fetch──► Python backend (FastAPI, 127.0.0.1:9090)
                                       │
                                       ├──kubeconfig──► K8s cluster(s)
                                       └──proxy──► Prometheus (optional, for CPU/MEM)
```

The backend reads `~/.kube/config`, exposes a REST API on `127.0.0.1:9090` (localhost only), and handles all cluster auth. The widget talks only to `http://localhost:9090`.

## Prerequisites

| Requirement | Notes |
|---|---|
| [Corsair iCUE](https://www.corsair.com/icue) 5.x+ | Widget host |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Runs the backend |
| `kubectl` configured with cluster access | `kubectl get nodes` should work |
| [`just`](https://github.com/casey/just) | Optional — task runner |
| `icue-packager:latest` Docker image | Only for building the `.icuewidget` package; build once from `../icue-widgetbuilder`: `just build-packager` |

### Optional cluster components

| Component | Feature unlocked |
|---|---|
| [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) | CPU/memory bars on the Health tab |
| [cert-manager](https://cert-manager.io) | TLS certificate expiry on the Health tab |

Without Prometheus the Health tab still shows node status, pod issues, and PVC health — CPU/MEM bars are hidden. Without cert-manager the certificate section is omitted.

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

## Navigation

The widget has three pages indicated by dots at the bottom of the screen:

- **● Overview** — cluster stats and warning events
- **● Workloads** — namespace list; tap a namespace to open the drill-down view
- **● Health** — node resources, pod issues, PVCs, certificates

The **drill-down view** (no dot — it overlays the Workloads page) shows all Deployments, StatefulSets, and DaemonSets in the selected namespace with ready/total replica counts. Tap the **←** back arrow in the header to return.

The **‹ ›** context switcher in the header lets you cycle between clusters on any of the three main pages.

## Widget properties

Configurable from the iCUE settings panel:

| Property | Type | Default | Description |
|---|---|---|---|
| Backend URL | Text | `http://localhost:9090` | URL of the k8s backend |
| Refresh (sec) | Slider 10–120 | `30` | Poll interval |
| Accent Color | Color | `#326ce5` | K8s blue |
| Background | Color | `#07080f` | Background fill |
| Transparency | Slider 0–100 | `100` | Background opacity |

## Backend API

| Endpoint | Description |
|---|---|
| `GET /contexts` | List all kubeconfig contexts |
| `GET /stats?context=<name>` | Node, pod, deployment, and namespace counts |
| `GET /events?context=<name>&limit=<1-100>` | Recent Warning/Error events (default 20) |
| `GET /workloads?context=<name>` | Pod and deployment counts grouped by namespace |
| `GET /namespace/{ns}/workloads?context=<name>` | Deployments, StatefulSets, and DaemonSets in a namespace |
| `GET /health-summary?context=<name>` | Node CPU/MEM (via Prometheus), crashing pods, PVC status, cert expiry |
| `GET /health` | Liveness check |

Set `DEBUG=true` in the backend environment to enable the Swagger UI at `/docs`.

### Pod issues filter

The `/health-summary` endpoint only reports pods that are **actively failing** (waiting state with a bad reason such as `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`) or that have had a restart within the **last 48 hours**. Pods with high lifetime restart counts that have been stable for longer are not reported.

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
