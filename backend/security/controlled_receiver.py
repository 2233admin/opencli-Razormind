"""Strict controlled-receiver v2 registry, MAC, receipt, and pinned transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.config import get_settings
from backend.security.url_guard import (
    PinnedAsyncHTTPTransport,
    SSRFValidationError,
    avalidate_public_url_and_ip,
)

MAC_VERSION = "v2"
_REQUEST_HEADER = "X-Controlled-Receiver-"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_HEADER_VALUE = 512
_MAX_RESPONSE_BYTES = 16 * 1024


class ControlledReceiverSecurityError(ValueError):
    """A registry, request, or receipt failed closed validation."""


@dataclass(frozen=True)
class ControlledReceiverEndpoint:
    identity: str
    receiver_identity: str
    url: str
    credential_reference: str
    request_key_id: str
    receipt_key_id: str
    allowed_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    durable_status: str


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_hash(value: Any | bytes) -> str:
    raw = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(raw).hexdigest()


def _json_setting(value: str, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ControlledReceiverSecurityError(f"Invalid server {name} configuration") from exc
    if not isinstance(parsed, dict):
        raise ControlledReceiverSecurityError(f"Invalid server {name} configuration")
    return parsed


def _strict_url(value: str) -> tuple[str, str, int, str]:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ControlledReceiverSecurityError(
            "Controlled receiver endpoint must be exact HTTPS without URL extras"
        )
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ControlledReceiverSecurityError(
            "Controlled receiver endpoint has invalid port"
        ) from exc
    if not parsed.path or not parsed.path.startswith("/"):
        raise ControlledReceiverSecurityError("Controlled receiver endpoint requires a fixed path")
    return parsed.scheme, parsed.hostname.lower(), port, parsed.path


def _validate_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ControlledReceiverSecurityError(f"Controlled receiver {field} is invalid")
    return value


def _validated_networks(
    value: Any,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    if not isinstance(value, list) or not value:
        raise ControlledReceiverSecurityError(
            "Controlled receiver registry requires a fixed network scope"
        )
    try:
        networks = tuple(ipaddress.ip_network(item, strict=True) for item in value)
    except ValueError as exc:
        raise ControlledReceiverSecurityError(
            "Controlled receiver registry network scope is invalid"
        ) from exc
    for network in networks:
        minimum_prefix = 24 if network.version == 4 else 64
        if (
            network.prefixlen < minimum_prefix
            or not network.network_address.is_global
            or not network.broadcast_address.is_global
        ):
            raise ControlledReceiverSecurityError(
                "Controlled receiver registry network scope is not a narrow global network"
            )
    return networks


def resolve_endpoint(identity: str, credential_reference: str) -> ControlledReceiverEndpoint:
    settings = get_settings()
    registry = _json_setting(settings.controlled_receiver_registry_json, "receiver registry")
    identity = _validate_identifier(identity, "endpoint identity")
    raw = registry.get(identity)
    if not isinstance(raw, dict):
        raise ControlledReceiverSecurityError(
            "Controlled receiver endpoint identity is not allowlisted"
        )
    required = {
        "url", "receiverIdentity", "credentialReference", "requestKeyId",
        "receiptKeyId", "allowedNetworks", "durableStatus",
    }
    if set(raw) != required:
        raise ControlledReceiverSecurityError("Controlled receiver registry entry is incomplete")
    url = raw["url"]
    receiver_identity = _validate_identifier(raw["receiverIdentity"], "identity")
    request_key_id = _validate_identifier(raw["requestKeyId"], "request key ID")
    receipt_key_id = _validate_identifier(raw["receiptKeyId"], "receipt key ID")
    expected_reference = _validate_identifier(raw["credentialReference"], "credential reference")
    durable_status = raw["durableStatus"]
    if not isinstance(url, str) or durable_status not in {"accepted", "rejected"}:
        raise ControlledReceiverSecurityError("Controlled receiver registry entry is incomplete")
    if credential_reference != expected_reference:
        raise ControlledReceiverSecurityError(
            "Frozen credential reference does not match controlled receiver registry"
        )
    networks = _validated_networks(raw["allowedNetworks"])
    _strict_url(url)
    return ControlledReceiverEndpoint(
        identity=identity,
        receiver_identity=receiver_identity,
        url=url,
        credential_reference=expected_reference,
        request_key_id=request_key_id,
        receipt_key_id=receipt_key_id,
        allowed_networks=networks,
        durable_status=durable_status,
    )


def endpoint_config_hash(endpoint: ControlledReceiverEndpoint) -> str:
    """Hash every non-secret registry field frozen into a target revision."""
    return canonical_hash(
        {
            "endpointIdentity": endpoint.identity,
            "url": endpoint.url,
            "receiverIdentity": endpoint.receiver_identity,
            "credentialReference": endpoint.credential_reference,
            "requestKeyId": endpoint.request_key_id,
            "receiptKeyId": endpoint.receipt_key_id,
            "allowedNetworks": [str(network) for network in endpoint.allowed_networks],
            "durableStatus": endpoint.durable_status,
        }
    )


def resolve_receiver_identity(receiver_identity: str) -> ControlledReceiverEndpoint:
    """Resolve an inbound receiver through exactly one configured registry entry."""
    receiver_identity = _validate_identifier(receiver_identity, "identity")
    registry = _json_setting(get_settings().controlled_receiver_registry_json, "receiver registry")
    matches = [
        (identity, value)
        for identity, value in registry.items()
        if isinstance(value, dict) and value.get("receiverIdentity") == receiver_identity
    ]
    if len(matches) != 1:
        raise ControlledReceiverSecurityError(
            "Controlled receiver identity is not uniquely allowlisted"
        )
    identity, value = matches[0]
    reference = value.get("credentialReference")
    return resolve_endpoint(identity, reference)


def _key(reference: str, key_id: str, setting_name: str) -> bytes:
    settings = get_settings()
    values = _json_setting(getattr(settings, setting_name), setting_name)
    value = values.get(
        reference
        if setting_name == "controlled_receiver_credentials_json"
        else key_id
    )
    if not isinstance(value, str):
        raise ControlledReceiverSecurityError("Controlled receiver key is unavailable")
    encoded = value.encode("utf-8")
    if len(encoded) < 32:
        raise ControlledReceiverSecurityError("Controlled receiver HMAC key is too short")
    return encoded


def _request_signing_bytes(
    *,
    body: bytes,
    key_id: str,
    timestamp: str,
    nonce: str,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
) -> bytes:
    return b"\n".join(
        (
            MAC_VERSION.encode(),
            key_id.encode(),
            timestamp.encode(),
            nonce.encode(),
            operation_id.encode(),
            decision_hash.encode(),
            payload_hash.encode(),
            canonical_hash(body).encode(),
        )
    )


def sign_request(
    *,
    body: bytes,
    credential_reference: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
) -> str:
    key = _key(credential_reference, key_id, "controlled_receiver_credentials_json")
    signing_bytes = _request_signing_bytes(
        body=body,
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        operation_id=operation_id,
        decision_hash=decision_hash,
        payload_hash=payload_hash,
    )
    signature = hmac.new(key, signing_bytes, hashlib.sha256).digest()
    return base64.b64encode(signature).decode("ascii")


def _header_operation_id(operation_id: str) -> str:
    """Safely carry arbitrary frozen #34 operation IDs without changing their canonical value."""
    return base64.urlsafe_b64encode(operation_id.encode("utf-8")).decode("ascii").rstrip("=")


def request_headers(
    *,
    body: bytes,
    endpoint: ControlledReceiverEndpoint,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = sign_request(
        body=body,
        credential_reference=endpoint.credential_reference,
        key_id=endpoint.request_key_id,
        timestamp=timestamp,
        nonce=nonce,
        operation_id=operation_id,
        decision_hash=decision_hash,
        payload_hash=payload_hash,
    )
    return {
        f"{_REQUEST_HEADER}Mac-Version": MAC_VERSION,
        f"{_REQUEST_HEADER}Key-Id": endpoint.request_key_id,
        f"{_REQUEST_HEADER}Timestamp": timestamp,
        f"{_REQUEST_HEADER}Nonce": nonce,
        f"{_REQUEST_HEADER}Operation-Id": _header_operation_id(operation_id),
        f"{_REQUEST_HEADER}Decision-Hash": decision_hash,
        f"{_REQUEST_HEADER}Payload-Hash": payload_hash,
        f"{_REQUEST_HEADER}Mac": signature,
        "Content-Type": "application/json",
    }


def verify_request(
    *,
    body: bytes,
    headers: Any,
    receiver_identity: str,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
) -> tuple[str, str]:
    """Verify MAC and prove the sender key is bound to this receiver identity."""
    endpoint = resolve_receiver_identity(receiver_identity)
    key_id = headers.get(f"{_REQUEST_HEADER}Key-Id", "")
    timestamp = headers.get(f"{_REQUEST_HEADER}Timestamp", "")
    nonce = headers.get(f"{_REQUEST_HEADER}Nonce", "")
    signature = headers.get(f"{_REQUEST_HEADER}Mac", "")
    bound_values = (
        key_id,
        timestamp,
        nonce,
        signature,
        operation_id,
        decision_hash,
        payload_hash,
    )
    if (
        headers.get(f"{_REQUEST_HEADER}Mac-Version") != MAC_VERSION
        or any(
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_HEADER_VALUE
            for value in bound_values
        )
        or _validate_identifier(key_id, "request key ID") != endpoint.request_key_id
        or _IDENTIFIER.fullmatch(nonce) is None
        or _HEX64.fullmatch(decision_hash) is None
        or _HEX64.fullmatch(payload_hash) is None
    ):
        raise ControlledReceiverSecurityError("Missing or invalid controlled receiver MAC fields")
    if any(
        headers.get(f"{_REQUEST_HEADER}{name}") != expected
        for name, expected in (
            ("Operation-Id", _header_operation_id(operation_id)),
            ("Decision-Hash", decision_hash),
            ("Payload-Hash", payload_hash),
        )
    ):
        raise ControlledReceiverSecurityError("Controlled receiver MAC binding mismatch")
    try:
        observed = int(timestamp)
        base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise ControlledReceiverSecurityError("Invalid controlled receiver MAC encoding") from exc
    if abs(int(time.time()) - observed) > get_settings().controlled_receiver_max_clock_skew_seconds:
        raise ControlledReceiverSecurityError("Controlled receiver timestamp outside allowed skew")
    key = _key(key_id, key_id, "controlled_receiver_inbound_keys_json")
    expected = base64.b64encode(
        hmac.new(
            key,
            _request_signing_bytes(
                body=body, key_id=key_id, timestamp=timestamp, nonce=nonce,
                operation_id=operation_id, decision_hash=decision_hash, payload_hash=payload_hash,
            ),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    if not secrets.compare_digest(expected, signature):
        raise ControlledReceiverSecurityError("Invalid controlled receiver MAC")
    return key_id, nonce


def receipt_payload(
    *,
    receiver_identity: str,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
    durable_status: str,
    receipt_id: str,
    issued_at: datetime,
) -> dict[str, str]:
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=UTC)
    return {
        "version": MAC_VERSION,
        "receiverIdentity": receiver_identity,
        "operationId": operation_id,
        "decisionHash": decision_hash,
        "payloadHash": payload_hash,
        "durableStatus": durable_status,
        "receiptId": receipt_id,
        "timestamp": str(int(issued_at.timestamp())),
    }


def sign_receipt(payload: dict[str, str], key_id: str) -> str:
    key = _key(key_id, key_id, "controlled_receiver_receipt_keys_json")
    signature = hmac.new(key, canonical_json(payload), hashlib.sha256).digest()
    return base64.b64encode(signature).decode("ascii")


def verify_receipt(
    *,
    receipt: Any,
    endpoint: ControlledReceiverEndpoint,
    operation_id: str,
    decision_hash: str,
    payload_hash: str,
) -> str:
    if not isinstance(receipt, dict) or set(receipt) != {
        "version", "receiverIdentity", "operationId", "decisionHash", "payloadHash",
        "durableStatus", "receiptId", "timestamp", "keyId", "signature",
    }:
        raise ControlledReceiverSecurityError("Missing controlled receiver receipt")
    signature = receipt["signature"]
    key_id = receipt["keyId"]
    fields = {
        key: receipt[key]
        for key in (
            "version", "receiverIdentity", "operationId", "decisionHash",
            "payloadHash", "durableStatus", "receiptId", "timestamp",
        )
    }
    if (
        key_id != endpoint.receipt_key_id
        or not isinstance(signature, str)
        or len(signature) > _MAX_HEADER_VALUE
        or any(
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_HEADER_VALUE
            for value in fields.values()
        )
        or _HEX64.fullmatch(fields["decisionHash"]) is None
        or _HEX64.fullmatch(fields["payloadHash"]) is None
        or _IDENTIFIER.fullmatch(fields["receiptId"]) is None
    ):
        raise ControlledReceiverSecurityError("Invalid controlled receiver receipt")
    if (
        fields["version"] != MAC_VERSION
        or fields["receiverIdentity"] != endpoint.receiver_identity
        or fields["operationId"] != operation_id
        or fields["decisionHash"] != decision_hash
        or fields["payloadHash"] != payload_hash
    ):
        raise ControlledReceiverSecurityError("Controlled receiver receipt binding mismatch")
    try:
        observed = int(fields["timestamp"])
        base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise ControlledReceiverSecurityError(
            "Invalid controlled receiver receipt encoding"
        ) from exc
    if abs(int(time.time()) - observed) > get_settings().controlled_receiver_max_clock_skew_seconds:
        raise ControlledReceiverSecurityError("Controlled receiver receipt outside allowed skew")
    expected = sign_receipt(fields, key_id)
    if not secrets.compare_digest(expected, signature):
        raise ControlledReceiverSecurityError("Invalid controlled receiver receipt signature")
    if fields["durableStatus"] not in {"accepted", "rejected"}:
        raise ControlledReceiverSecurityError("Invalid controlled receiver durable status")
    return fields["durableStatus"]


async def pinned_post(
    endpoint: ControlledReceiverEndpoint,
    body: bytes,
    headers: dict[str, str],
    *,
    timeout_seconds: float,
    status_query: bool = False,
) -> httpx.Response:
    url = endpoint.url[:-len("/deliver")] + "/status" if status_query else endpoint.url
    if status_query and not endpoint.url.endswith("/deliver"):
        raise ControlledReceiverSecurityError(
            "Controlled receiver delivery path cannot derive fixed status path"
        )
    try:
        validated_url, ips = await avalidate_public_url_and_ip(url)
    except SSRFValidationError as exc:
        raise ControlledReceiverSecurityError(
            "Controlled receiver endpoint failed public-address validation"
        ) from exc
    if validated_url != url:
        raise ControlledReceiverSecurityError("Controlled receiver endpoint normalization changed")
    if any(
        not any(
            ipaddress.ip_address(value) in network
            for network in endpoint.allowed_networks
        )
        for value in ips
    ):
        raise ControlledReceiverSecurityError(
            "Controlled receiver DNS answer is outside its fixed network scope"
        )
    _, host, _, _ = _strict_url(url)
    transport = PinnedAsyncHTTPTransport(
        host, ips, verify=os.environ.get("SSL_CERT_FILE") or True
    )
    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        async with client.stream("POST", url, content=body, headers=headers) as response:
            if response.is_redirect:
                raise ControlledReceiverSecurityError("Controlled receiver redirects are forbidden")
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    if int(declared_length) > _MAX_RESPONSE_BYTES:
                        raise ControlledReceiverSecurityError(
                            "Controlled receiver response is too large"
                        )
                except ValueError as exc:
                    raise ControlledReceiverSecurityError(
                        "Controlled receiver response length is invalid"
                    ) from exc
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > _MAX_RESPONSE_BYTES:
                    raise ControlledReceiverSecurityError(
                        "Controlled receiver response is too large"
                    )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=response.request,
            )
