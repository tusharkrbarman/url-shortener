import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class PermanentValidationError(Exception):
    pass


class TransientValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidationResult:
    metadata: dict


PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip in network for network in PRIVATE_NETWORKS) or ip.is_private


class UrlValidator:
    def __init__(self, *, enable_network_checks: bool = False):
        self.enable_network_checks = enable_network_checks

    def validate(self, url: str) -> ValidationResult:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise PermanentValidationError("URL host is missing.")
        lowered = host.lower()
        if lowered.endswith(".invalid") or lowered == "invalid":
            raise PermanentValidationError("Destination host is invalid.")
        if "transient" in lowered:
            raise TransientValidationError("Temporary validation provider failure.")
        if self.enable_network_checks:
            self._check_public_host(host)
        return ValidationResult(metadata={"validatedHost": lowered})

    def _check_public_host(self, host: str):
        try:
            addresses = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise TransientValidationError("DNS resolution failed.") from exc
        for address in addresses:
            ip = address[4][0]
            if _is_private_address(ip):
                raise PermanentValidationError("Destination resolves to a private address.")

