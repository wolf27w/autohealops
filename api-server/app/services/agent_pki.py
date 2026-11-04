# -*- coding: utf-8 -*-
"""Agent 隧道 PKI（2026-11-04）：平台自建 CA，签发网关服务端证书与 agent 客户端证书。

- CA 私钥只留在平台本机（默认 `data/agent-ca/`，0600），不进 git；
- 网关用服务端证书 + 要求客户端证书（mTLS）；
- agent 接入时现场签发客户端证书（CN=autohealops-agent-<cluster_id>），塞进 install.yaml 的 Secret。
"""
import ipaddress
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.agent_pki")

_CA_DIR = Path(settings.AGENT_CA_DIR).expanduser()
if not _CA_DIR.is_absolute():
    # 相对路径按仓库根解析（api-server/../data/agent-ca）
    _CA_DIR = (Path(__file__).resolve().parents[3] / _CA_DIR).resolve()

_CA_CRT = _CA_DIR / "ca.crt"
_CA_KEY = _CA_DIR / "ca.key"
_SRV_CRT = _CA_DIR / "server.crt"
_SRV_KEY = _CA_DIR / "server.key"


def ca_dir() -> Path:
    return _CA_DIR


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _load_or_create_ca():
    """加载或生成平台自建 CA（ECDSA P-256，10 年）"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    if _CA_CRT.exists() and _CA_KEY.exists():
        key = serialization.load_pem_private_key(_CA_KEY.read_bytes(), password=None)
        return x509.load_pem_x509_certificate(_CA_CRT.read_bytes()), key

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AutoHealOps"),
        x509.NameAttribute(NameOID.COMMON_NAME, "AutoHealOps Agent CA"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(_CA_DIR / "ca.key", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    _CA_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(_CA_CRT, stat.S_IRUSR | stat.S_IWUSR)
    logger.warning("已生成平台自建 CA：%s（私钥仅本机，务必备份）", _CA_CRT)
    return cert, key


def ensure_server_cert(hosts: list[str] | None = None) -> tuple[str, str]:
    """确保网关服务端证书存在（SAN 含传入 host/IP），返回 (cert_path, key_path)"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_cert, ca_key = _load_or_create_ca()
    hosts = [h for h in (hosts or []) if h] + ["localhost", "127.0.0.1"]
    seen, sans = set(), []
    for h in hosts:
        if h in seen:
            continue
        seen.add(h)
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))

    if _SRV_CRT.exists() and _SRV_KEY.exists():
        try:
            cur = x509.load_pem_x509_certificate(_SRV_CRT.read_bytes())
            existing = {str(g.value) for g in cur.extensions.get_extension_for_class(x509.SubjectAlternativeName).value}
            if all((str(h) in existing) for h in hosts) and cur.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=30):
                return str(_SRV_CRT), str(_SRV_KEY)
        except Exception:  # noqa: BLE001 证书损坏/格式变化 → 重签
            pass

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AutoHealOps"),
            x509.NameAttribute(NameOID.COMMON_NAME, hosts[0]),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=730))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(_SRV_KEY, key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    _SRV_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("已签发网关服务端证书 SAN=%s", ", ".join(str(s.value) for s in sans))
    return str(_SRV_CRT), str(_SRV_KEY)


def ca_cert_pem() -> str:
    ca_cert, _ = _load_or_create_ca()
    from cryptography.hazmat.primitives import serialization
    return ca_cert.public_bytes(serialization.Encoding.PEM).decode()


def issue_client_cert(cluster_id: int, days: int = 1825) -> tuple[str, str]:
    """为某集群签发 agent 客户端证书（mTLS），返回 (cert_pem, key_pem)"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_cert, ca_key = _load_or_create_ca()
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AutoHealOps"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"autohealops-agent-{cluster_id}"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption()).decode(),
    )
