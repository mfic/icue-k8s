from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client, config
from kubernetes.config.kube_config import KUBE_CONFIG_DEFAULT_LOCATION
from datetime import datetime, timezone
import yaml, os, logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true")

app = FastAPI(
    title="iCUE K8s Backend",
    docs_url="/docs" if _DEBUG else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _DEBUG else None,
)

# Wildcard origin is intentional: the iCUE widget framework serves files from
# non-HTTP schemes that can't be allowlisted. The 127.0.0.1 bind is the real
# network boundary. Set DEBUG=true to enable /docs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

KUBECONFIG_PATH = os.environ.get("KUBECONFIG", KUBE_CONFIG_DEFAULT_LOCATION)


def get_api(context: str | None, api_class):
    cfg = client.Configuration()
    config.load_kube_config(config_file=KUBECONFIG_PATH, context=context, client_configuration=cfg)
    return api_class(client.ApiClient(cfg))


def parse_kubeconfig() -> dict:
    path = os.path.expanduser(KUBECONFIG_PATH)
    with open(path) as f:
        return yaml.safe_load(f)


def validate_context(context: str | None) -> str | None:
    if context is None:
        return None
    kc = parse_kubeconfig()
    valid = {c["name"] for c in kc.get("contexts", [])}
    if context not in valid:
        raise HTTPException(status_code=400, detail="Unknown context")
    return context


@app.get("/contexts")
def list_contexts():
    try:
        kc = parse_kubeconfig()
        current = kc.get("current-context", "")
        contexts = [c["name"] for c in kc.get("contexts", [])]
        return {"contexts": contexts, "current": current}
    except HTTPException:
        raise
    except Exception:
        log.exception("contexts error")
        raise HTTPException(status_code=500, detail="Internal error — check server logs")


@app.get("/stats")
def get_stats(context: str | None = Query(None)):
    context = validate_context(context)
    try:
        v1      = get_api(context, client.CoreV1Api)
        apps_v1 = get_api(context, client.AppsV1Api)

        nodes      = v1.list_node()
        node_total = len(nodes.items)
        node_ready = sum(
            1 for n in nodes.items
            for c in (n.status.conditions or [])
            if c.type == "Ready" and c.status == "True"
        )

        pods       = v1.list_pod_for_all_namespaces(limit=1000)
        pod_phases = {}
        for p in pods.items:
            phase = p.status.phase or "Unknown"
            pod_phases[phase] = pod_phases.get(phase, 0) + 1

        deploys      = apps_v1.list_deployment_for_all_namespaces()
        dep_total    = len(deploys.items)
        dep_ready    = sum(
            1 for d in deploys.items
            if (d.status.ready_replicas or 0) >= (d.status.replicas or 0) > 0
        )
        dep_degraded = dep_total - dep_ready

        namespaces = v1.list_namespace()
        ns_count   = len(namespaces.items)

        kc       = parse_kubeconfig()
        ctx_name = context or kc.get("current-context", "unknown")

        return {
            "context":     ctx_name,
            "nodes":       {"ready": node_ready,  "total": node_total},
            "pods":        pod_phases,
            "deployments": {"ready": dep_ready, "degraded": dep_degraded, "total": dep_total},
            "namespaces":  ns_count,
        }
    except HTTPException:
        raise
    except Exception:
        log.exception("stats error")
        raise HTTPException(status_code=500, detail="Internal error — check server logs")


@app.get("/events")
def get_events(
    context: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    context = validate_context(context)
    try:
        v1     = get_api(context, client.CoreV1Api)
        events = v1.list_event_for_all_namespaces(
            field_selector="type!=Normal",
            limit=100,
        )

        def ts(e):
            t = e.last_timestamp or e.event_time
            if t is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if hasattr(t, "tzinfo") and t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t

        sorted_events = sorted(events.items, key=ts, reverse=True)[:limit]

        result = []
        for e in sorted_events:
            t = ts(e)
            result.append({
                "namespace": e.metadata.namespace,
                "name":      e.involved_object.name,
                "kind":      e.involved_object.kind,
                "reason":    e.reason or "",
                "message":   (e.message or "")[:120],
                "type":      e.type or "Warning",
                "count":     e.count or 1,
                "time":      t.isoformat() if t != datetime.min.replace(tzinfo=timezone.utc) else None,
            })
        return result
    except HTTPException:
        raise
    except Exception:
        log.exception("events error")
        raise HTTPException(status_code=500, detail="Internal error — check server logs")


@app.get("/workloads")
def get_workloads(context: str | None = Query(None)):
    context = validate_context(context)
    try:
        v1      = get_api(context, client.CoreV1Api)
        apps_v1 = get_api(context, client.AppsV1Api)

        namespaces = [ns.metadata.name for ns in v1.list_namespace().items]

        pods = v1.list_pod_for_all_namespaces(limit=1000)
        pod_map: dict[str, dict] = {}
        for p in pods.items:
            ns = p.metadata.namespace
            if ns not in pod_map:
                pod_map[ns] = {"running": 0, "total": 0}
            pod_map[ns]["total"] += 1
            if p.status.phase == "Running":
                pod_map[ns]["running"] += 1

        deploys = apps_v1.list_deployment_for_all_namespaces()
        dep_map: dict[str, dict] = {}
        for d in deploys.items:
            ns = d.metadata.namespace
            if ns not in dep_map:
                dep_map[ns] = {"ready": 0, "total": 0}
            dep_map[ns]["total"] += 1
            if (d.status.ready_replicas or 0) >= (d.status.replicas or 0) > 0:
                dep_map[ns]["ready"] += 1

        result = []
        for ns in sorted(namespaces):
            pods_ns = pod_map.get(ns, {"running": 0, "total": 0})
            deps_ns = dep_map.get(ns, {"ready": 0, "total": 0})
            if pods_ns["total"] == 0 and deps_ns["total"] == 0:
                continue
            result.append({
                "namespace":   ns,
                "pods":        pods_ns,
                "deployments": deps_ns,
            })
        return result
    except HTTPException:
        raise
    except Exception:
        log.exception("workloads error")
        raise HTTPException(status_code=500, detail="Internal error — check server logs")


@app.get("/namespace/{ns}/workloads")
def get_namespace_workloads(ns: str, context: str | None = Query(None)):
    context = validate_context(context)
    try:
        apps_v1 = get_api(context, client.AppsV1Api)
        result  = []

        for d in apps_v1.list_namespaced_deployment(ns).items:
            result.append({
                "kind":    "Deployment",
                "name":    d.metadata.name,
                "ready":   d.status.ready_replicas or 0,
                "desired": d.status.replicas or 0,
            })
        for s in apps_v1.list_namespaced_stateful_set(ns).items:
            result.append({
                "kind":    "StatefulSet",
                "name":    s.metadata.name,
                "ready":   s.status.ready_replicas or 0,
                "desired": s.spec.replicas or 0,
            })
        for d in apps_v1.list_namespaced_daemon_set(ns).items:
            result.append({
                "kind":    "DaemonSet",
                "name":    d.metadata.name,
                "ready":   d.status.number_ready or 0,
                "desired": d.status.desired_number_scheduled or 0,
            })

        return sorted(result, key=lambda x: (x["kind"], x["name"]))
    except HTTPException:
        raise
    except Exception:
        log.exception("namespace workloads error")
        raise HTTPException(status_code=500, detail="Internal error — check server logs")


@app.get("/health")
def health():
    return {"status": "ok"}
