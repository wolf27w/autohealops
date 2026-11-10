#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从项目根 .env 生成 k8s 配置物料（ConfigMap + Secret）。

用法：
    ./scripts/gen-k8s-config.sh                 # 生成 k8s/generated/{configmap,secret}.yaml
    ./scripts/gen-k8s-config.sh --check        # 额外跑 kubectl --dry-run=client 校验
    ./scripts/gen-k8s-config.sh --print        # 只检查覆盖度，不写文件

设计：
  - 唯一配置源是项目根 .env（缺项回落到 .env.example）—— 不再手工维护两份配置；
  - 敏感项（连接串/密钥/凭据）→ Secret；其余 → ConfigMap；
  - 集群内地址自动覆盖（localhost → ClusterIP 服务名）；
  - 生成前做「覆盖度校验」：代码里所有配置项必须出现在生成物里，缺一个就报错退出，
    这样 .env 加了新配置、K8s 物料不会静默漏掉。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "k8s" / "generated"

# 敏感项：一律进 Secret
SECRET_KEYS = {
    "DATABASE_URL", "REDIS_URL", "JWT_SECRET_KEY", "ENCRYPTION_KEY",
    "ADMIN_INITIAL_PASSWORD", "REGISTRY_USERNAME", "REGISTRY_PASSWORD", "REGISTRY_CREDS",
    "AGENT_REGISTRY_PASSWORD",
}

# 集群内地址覆盖（.env 里是本地开发地址，进集群必须换 Service DNS）
OVERRIDES = {
    "K8S_EXECUTOR_GRPC_ADDR": "k8s-executor:9090",
    "EXECUTOR_LOGSTREAM_ADDR": "http://k8s-executor:9099",
    "AI_SERVICE_HTTP_ADDR": "http://ai-service:9000",
    "LOGSTREAM_ADDR": ":9099",
    "API_SERVER_URL": "http://api-server:8080",
    "AGENT_CA_DIR": "/var/lib/autohealops/agent-ca",
}

# 各服务用哪些配置（ConfigMap 里的项；Secret 全服务共享一整包）
API_CONFIGMAP = [
    "K8S_EXECUTOR_GRPC_ADDR", "EXECUTOR_LOGSTREAM_ADDR", "AI_SERVICE_HTTP_ADDR",
    "EXECUTOR_MAX_CONCURRENCY", "JWT_ALGORITHM", "JWT_EXPIRE_HOURS", "LOG_LEVEL",
    "CORS_ORIGINS", "COMMAND_WHITELIST", "JVM_JATTACH_URL", "JVM_JATTACH_PATH",
    "AGENT_IMAGE", "AGENT_NAMESPACE", "AGENT_IMAGE_PULL_SECRET", "AGENT_GATEWAY_ADDR",
    "AGENT_GATEWAY_PUBLIC_ADDR", "PLATFORM_PUBLIC_URL", "AGENT_TLS_ENABLED", "AGENT_CA_DIR",
    "AGENT_REGISTRY_USERNAME",
    "POD_METRICS_RAW_RETENTION_HOURS", "POD_METRICS_ROLLUP_RETENTION_DAYS",
]
EXECUTOR_CONFIGMAP = ["GRPC_PORT", "LOGSTREAM_ADDR", "LOG_LEVEL", "API_SERVER_URL", "COMMAND_WHITELIST",
                      "POD_METRICS_RAW_RETENTION_HOURS"]
AI_CONFIGMAP = ["LOG_LEVEL", "PROMETHEUS_URL", "DAILY_COST_LIMIT", "SINGLE_ALERT_COST_LIMIT"]


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def code_keys() -> dict[str, set[str]]:
    """从源码里解析出每个服务实际读取的配置项（避免手工清单漂移）"""
    res: dict[str, set[str]] = {"api-server": set(), "ai-service": set(), "k8s-executor": set()}

    for svc, path in (
        ("api-server", ROOT / "api-server/app/core/config.py"),
        ("ai-service", ROOT / "ai-service/app/core/config.py"),
    ):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^    ([A-Z][A-Z0-9_]*)\s*:", line)
            if m:
                res[svc].add(m.group(1))

    go = ROOT / "k8s-executor/internal/config/config.go"
    if go.exists():
        res["k8s-executor"] |= set(re.findall(r'GetString\("([A-Z][A-Z0-9_]*)"\)', go.read_text(encoding="utf-8")))
    return res


def build_maps(env: dict[str, str]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    cfg = {
        "api-server-config": {k: OVERRIDES.get(k, env.get(k, "")) for k in API_CONFIGMAP},
        "k8s-executor-config": {k: OVERRIDES.get(k, env.get(k, "")) for k in EXECUTOR_CONFIGMAP},
        "ai-service-config": {k: OVERRIDES.get(k, env.get(k, "")) for k in AI_CONFIGMAP},
    }
    # Secret 里允许为空（如 ADMIN_INITIAL_PASSWORD / REGISTRY_* 可选）
    sec = {k: env.get(k, "") for k in sorted(SECRET_KEYS)}
    return cfg, sec


def verify(cfg: dict[str, dict[str, str]], sec: dict[str, str]) -> list[str]:
    need = code_keys()
    have = {
        "api-server": set(cfg["api-server-config"]) | set(sec),
        "ai-service": set(cfg["ai-service-config"]) | set(sec),
        "k8s-executor": set(cfg["k8s-executor-config"]) | set(sec),
    }
    problems: list[str] = []
    for svc, keys in need.items():
        missing = sorted(keys - have[svc])
        if missing:
            problems.append(f"{svc} 缺少配置项: {', '.join(missing)}")
    return problems


def dump_yaml(docs: list[dict]) -> str:
    import yaml  # PyYAML（api-server venv 已装）

    return yaml.safe_dump_all(docs, allow_unicode=True, sort_keys=False, default_flow_style=False)


HEADER = """# ============================================================
# 自动生成，请勿手改 —— 由 scripts/gen-k8s-config.sh 从项目根 .env 生成
# 生成时间：{ts}
# 重新生成：./scripts/gen-k8s-config.sh
# ============================================================
"""


def _warn_private_addr(cfg: dict[str, dict[str, str]]) -> None:
    """网关/平台地址若是内网 IP，外部集群连不上 —— 提醒但不阻断（本地调试就是内网）"""
    import ipaddress

    for key in ("AGENT_GATEWAY_PUBLIC_ADDR", "PLATFORM_PUBLIC_URL"):
        val = (cfg.get("api-server-config") or {}).get(key) or ""
        host = val.split("://")[-1].split(":")[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback:
            print(f"⚠ {key}={val} 是内网地址：外部集群接入前请改成公网地址或域名", file=sys.stderr)


def main() -> int:
    env = parse_env_file(ROOT / ".env")
    if not env:
        print("⚠ 找不到项目根 .env，改用 .env.example 做覆盖度校验（只生成模板值）", file=sys.stderr)
    fallback = parse_env_file(ROOT / ".env.example")
    if not env and not fallback:
        print("✗ 既没有 .env 也没有 .env.example，无法生成", file=sys.stderr)
        return 1
    for k, v in fallback.items():
        env.setdefault(k, v)

    cfg, sec = build_maps(env)
    problems = verify(cfg, sec)
    if problems:
        print("✗ 配置覆盖度校验失败：", file=sys.stderr)
        for p in problems:
            print("   - " + p, file=sys.stderr)
        print("   → 把缺失项补进本脚本的 API_CONFIGMAP / EXECUTOR_CONFIGMAP / AI_CONFIGMAP 或 SECRET_KEYS",
              file=sys.stderr)
        return 2

    if "--print" in sys.argv:
        for name, data in cfg.items():
            print(f"{name}: {len(data)} 项")
        print(f"autohealops-secrets: {len(sec)} 项")
        print("✓ 覆盖度校验通过（代码里的配置项全部有归属）")
        _warn_private_addr(cfg)
        return 0

    import datetime

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cm_docs = [
        {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": "autohealops"},
            "data": data,
        }
        for name, data in cfg.items()
    ]
    sec_doc = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "autohealops-secrets", "namespace": "autohealops"},
        "type": "Opaque",
        "stringData": sec,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "configmap.yaml").write_text(HEADER.format(ts=ts) + dump_yaml(cm_docs), encoding="utf-8")
    (OUT_DIR / "secret.yaml").write_text(HEADER.format(ts=ts) + dump_yaml([sec_doc]), encoding="utf-8")
    print(f"✓ 已生成 {OUT_DIR.relative_to(ROOT)}/configmap.yaml（{sum(len(d) for d in cfg.values())} 项）")
    print(f"✓ 已生成 {OUT_DIR.relative_to(ROOT)}/secret.yaml（{len(sec)} 项，敏感值不回显）")
    _warn_private_addr(cfg)

    if "--check" in sys.argv:
        import yaml

        files = [str(OUT_DIR / "configmap.yaml"), str(OUT_DIR / "secret.yaml")]
        files += [str(p) for p in sorted((ROOT / "k8s").glob("*.yaml"))]

        # 1) 本地 YAML 结构校验（不依赖集群）
        bad: list[str] = []
        total = 0
        for f in files:
            try:
                docs = [d for d in yaml.safe_load_all(Path(f).read_text(encoding="utf-8")) if d]
            except yaml.YAMLError as e:
                bad.append(f"{Path(f).name}: YAML 解析失败 {e}")
                continue
            if not docs:
                bad.append(f"{Path(f).name}: 没有对象")
                continue
            for d in docs:
                total += 1
                if not isinstance(d, dict):
                    bad.append(f"{Path(f).name}: 文档不是对象")
                    continue
                for k in ("apiVersion", "kind"):
                    if k not in d:
                        bad.append(f"{Path(f).name}: 缺少 {k}")
                if not (d.get("metadata") or {}).get("name"):
                    bad.append(f"{Path(f).name}: 缺少 metadata.name")
        if bad:
            print("✗ YAML 结构校验失败：", file=sys.stderr)
            for b in bad:
                print("   - " + b, file=sys.stderr)
            return 3
        print(f"✓ YAML 结构校验通过（{total} 个对象）")

        # 2) 有可用集群时再做 API 级 dry-run
        probe = subprocess.run(["kubectl", "cluster-info"], capture_output=True, text=True)
        if probe.returncode != 0:
            print("• 当前 kubeconfig 连不上集群，跳过 kubectl API dry-run（结构校验已通过）")
            return 0
        cmd = ["kubectl", "apply", "--dry-run=server"]
        for f in files:
            cmd += ["-f", f]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            print("✗ kubectl 校验失败", file=sys.stderr)
            return 3
        print("✓ kubectl --dry-run=server 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
