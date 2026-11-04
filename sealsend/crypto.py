"""Proof layer: Ed25519 signatures, the Mapping Lock and the Merkle batch seal."""
import base64
import hashlib
import hmac
import json
import os
import threading

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import config

_lock = threading.Lock()
_priv: Ed25519PrivateKey | None = None


def canonical(obj) -> bytes:
    """Deterministic JSON bytes: the exact thing that gets signed or hashed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- keys
def private_key() -> Ed25519PrivateKey:
    global _priv
    with _lock:
        if _priv is None:
            path = config.SIGNING_KEY_PATH
            if path.exists():
                _priv = serialization.load_pem_private_key(path.read_bytes(), password=None)
            else:
                _priv = Ed25519PrivateKey.generate()
                path.parent.mkdir(parents=True, exist_ok=True)
                pem = _priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption())
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(pem)
        return _priv


def reset_keys() -> None:
    global _priv
    with _lock:
        _priv = None


def public_key() -> Ed25519PublicKey:
    return private_key().public_key()


def public_raw() -> bytes:
    return public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_pem() -> str:
    return public_key().public_bytes(serialization.Encoding.PEM,
                                     serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def fingerprint() -> str:
    h = sha256_hex(public_raw())[:16].upper()
    return " ".join(h[i:i + 4] for i in range(0, 16, 4))


def sign(data: bytes) -> str:
    return base64.urlsafe_b64encode(private_key().sign(data)).decode().rstrip("=")


def verify(data: bytes, signature: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        public_key().verify(raw, data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- mapping lock
def _lock_secret() -> bytes:
    raw = private_key().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                      serialization.NoEncryption())
    return hashlib.sha256(b"sealsend/mapping-lock/v1" + raw).digest()


def mapping_lock(student_id: str, email: str, event: str, cert_code: str) -> str:
    """Hidden keyed hash binding a certificate to exactly one student + address + event.

    It is written into the PDF at generation time and recomputed from the recipient's
    record right before sending; any mismatch means the attachment is not theirs.
    """
    msg = canonical([student_id.strip().lower(), email.strip().lower(), event.strip().lower(), cert_code])
    return hmac.new(_lock_secret(), msg, hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------- merkle batch seal
def leaf_hash(cert_code: str, pdf_sha256: str, lock: str) -> str:
    return hashlib.sha256(b"\x00" + canonical([cert_code, pdf_sha256, lock])).hexdigest()


def _node(left: str, right: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(_node(level[i], level[i + 1]) if i + 1 < len(level) else level[i])  # odd node promoted
        level = nxt
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[list[str]]:
    """Sibling path for leaves[index]: [["L"|"R", hash], ...] from the leaf up."""
    proof, level, i = [], list(leaves), index
    while len(level) > 1:
        sib = i ^ 1
        if sib < len(level):
            proof.append(["L" if sib < i else "R", level[sib]])
        nxt = []
        for j in range(0, len(level), 2):
            nxt.append(_node(level[j], level[j + 1]) if j + 1 < len(level) else level[j])
        level, i = nxt, i // 2
    return proof


def verify_proof(leaf: str, proof: list[list[str]], root: str) -> bool:
    h = leaf
    for side, sib in proof:
        h = _node(sib, h) if side == "L" else _node(h, sib)
    return hmac.compare_digest(h, root)
