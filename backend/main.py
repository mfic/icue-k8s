from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client, config
from kubernetes.config.kube_config import KUBE_CONFIG_DEFAULT_LOCATION
from datetime import datetime, timezone
import yaml, os, logging, re, json, urllib.parse

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


def _make_api_client(context: str | None) -> client.ApiClient:
    cfg = client.Configuration()
    config.load_kube_config(config_file=KUBECONFIG_PATH, context=context, client_configuration=cfg)
    return client.ApiClient(cfg)

def get_api(context: str | None, api_class):
    return api_class(_make_api_client(context))


PROM_SVC = (
    "/api/v1/namespaces/monitoring/services/"
    "http:kube-prometheus-stack-prometheus:9090/proxy"
)

def _prom_query(api_client: client.ApiClient, query: str) -> list:
    q    = urllib.parse.quote(query, safe='')
    url  = f"{api_client.configuration.host.rstrip('/')}{PROM_SVC}/api/v1/query?query={q}"
    hdrs: dict = {'Accept': 'application/json', 'User-Agent': api_client.user_agent}
    api_client.update_params_for_auth(hdrs, [], ['BearerToken'])
    resp = api_client.rest_client.GET(url, headers=hdrs, _preload_content=True, _request_timeout=10)
    data = json.loads(resp.data)
    if data.get('status') != 'success':
        raise ValueError(f"Prometheus: {data}")
    return data['data']['result']


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


def _parse_cpu(s: str) -> int:
    if s.endswith('m'):
        return int(s[:-1])
    return int(float(s) * 1000)

def _parse_mem(s: str) -> int:
    m = re.match(r'^(\d+)(Ki|Mi|Gi|Ti|K|M|G|T)?$', s)
    if not m:
        return int(s)
    v, unit = int(m.group(1)), m.group(2) or ''
    return v * {'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4,
                'K':1000,'M':1000**2,'G':1000**3,'T':1000**4}.get(unit, 1)

def _node_role(node) -> str:
    labels = node.metadata.labels or {}
    if any(k in labels for k in ('node-role.kubernetes.io/control-plane',
                                  'node-role.kubernetes.io/master')):
        return 'master'
    return 'worker'


@app.get("/health-summary")
def get_health_summary(context: str | None = Query(None)):
    context = validate_context(context)
    try:
        v1      = get_api(context, client.CoreV1Api)
        apps_v1 = get_api(context, client.AppsV1Api)

        # ── Nodes ────────────────────────────────────────────────────────────
        nodes_raw = v1.list_node().items

        # Build IP → node-name map for matching Prometheus instance labels
        ip_to_node: dict[str, str] = {}
        for n in nodes_raw:
            for addr in (n.status.addresses or []):
                if addr.type == 'InternalIP':
                    ip_to_node[addr.address] = n.metadata.name

        # Try Prometheus for CPU / memory percentages
        cpu_pct: dict[str, float] = {}
        mem_pct: dict[str, float] = {}
        metrics_ok = False
        try:
            ac = _make_api_client(context)
            for r in _prom_query(ac, '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'):
                ip = r['metric'].get('instance', '').split(':')[0]
                if ip in ip_to_node:
                    cpu_pct[ip_to_node[ip]] = round(float(r['value'][1]), 1)
            for r in _prom_query(ac, '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100'):
                ip = r['metric'].get('instance', '').split(':')[0]
                if ip in ip_to_node:
                    mem_pct[ip_to_node[ip]] = round(float(r['value'][1]), 1)
            metrics_ok = bool(cpu_pct)
        except Exception:
            log.warning("Prometheus metrics unavailable")

        nodes_out = []
        for n in sorted(nodes_raw, key=lambda x: (_node_role(x) != 'master', x.metadata.name)):
            ready = any(c.type == 'Ready' and c.status == 'True'
                        for c in (n.status.conditions or []))
            conditions = [c.type for c in (n.status.conditions or [])
                          if c.type != 'Ready' and c.status == 'True']
            name = n.metadata.name
            nodes_out.append({
                'name':       name,
                'role':       _node_role(n),
                'ready':      ready,
                'conditions': conditions,
                'cpu_pct':    cpu_pct.get(name),
                'mem_pct':    mem_pct.get(name),
            })

        # ── Crashing / restarting pods ───────────────────────────────────────
        BAD_REASONS = {'CrashLoopBackOff', 'OOMKilled', 'Error',
                       'ImagePullBackOff', 'ErrImagePull'}
        now_utc   = datetime.now(timezone.utc)
        cutoff_48h = 48 * 3600
        crashing  = []
        for p in v1.list_pod_for_all_namespaces(limit=1000).items:
            for cs in (p.status.container_statuses or []):
                reason = (cs.state.waiting.reason
                          if cs.state and cs.state.waiting else None)
                # Always surface pods actively stuck in a bad state
                if reason in BAD_REASONS:
                    crashing.append({
                        'namespace': p.metadata.namespace,
                        'name':      p.metadata.name,
                        'reason':    reason,
                        'restarts':  cs.restart_count,
                    })
                    break
                # High-restart pods only if the last failure was within 48 h
                if cs.restart_count > 5:
                    fin = None
                    if cs.last_state and cs.last_state.terminated:
                        fin = cs.last_state.terminated.finished_at
                    if fin is not None:
                        if fin.tzinfo is None:
                            fin = fin.replace(tzinfo=timezone.utc)
                        if (now_utc - fin).total_seconds() < cutoff_48h:
                            crashing.append({
                                'namespace': p.metadata.namespace,
                                'name':      p.metadata.name,
                                'reason':    'Restarting',
                                'restarts':  cs.restart_count,
                            })
                            break

        # ── PVCs ─────────────────────────────────────────────────────────────
        pvc_counts: dict[str, int] = {'Bound': 0, 'Pending': 0, 'Lost': 0}
        pvc_issues = []
        for pvc in v1.list_persistent_volume_claim_for_all_namespaces().items:
            phase = pvc.status.phase or 'Unknown'
            pvc_counts[phase] = pvc_counts.get(phase, 0) + 1
            if phase in ('Pending', 'Lost'):
                pvc_issues.append({
                    'namespace': pvc.metadata.namespace,
                    'name':      pvc.metadata.name,
                    'phase':     phase,
                })

        # ── Certificates (cert-manager) ──────────────────────────────────────
        certs_ok   = False
        cert_expiring = []
        try:
            cust  = get_api(context, client.CustomObjectsApi)
            clist = cust.list_cluster_custom_object("cert-manager.io", "v1", "certificates")
            certs_ok = True
            now = datetime.now(timezone.utc)
            for cert in clist.get('items', []):
                not_after = (cert.get('status') or {}).get('notAfter')
                if not_after:
                    exp = datetime.fromisoformat(not_after.replace('Z', '+00:00'))
                    days = (exp - now).days
                    if days < 30:
                        cert_expiring.append({
                            'namespace': cert['metadata']['namespace'],
                            'name':      cert['metadata']['name'],
                            'days':      days,
                        })
            cert_expiring.sort(key=lambda x: x['days'])
        except Exception:
            pass

        return {
            'nodes':           nodes_out,
            'metrics_available': metrics_ok,
            'crashing_pods':   sorted(crashing, key=lambda x: -x['restarts']),
            'pvcs': {
                'bound':   pvc_counts.get('Bound',   0),
                'pending': pvc_counts.get('Pending', 0),
                'lost':    pvc_counts.get('Lost',    0),
                'issues':  pvc_issues,
            },
            'certs': {
                'available': certs_ok,
                'expiring':  cert_expiring,
            },
        }
    except HTTPException:
        raise
    except Exception:
        log.exception("health-summary error")
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
