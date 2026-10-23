# -*- coding: utf-8 -*-
"""Prefer IPv4 for outbound HTTP(S).

Broker / office networks sometimes black-hole IPv6 (TCP stays in SYN_SENT).
requests/urllib3 will try AAAA first; a short connect timeout still feels like
a multi-second freeze per host. Filtering getaddrinfo to AF_INET avoids that.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator, Tuple

# (connect, read) — keep connect short so black-holed routes fail fast
DEFAULT_TIMEOUT: Tuple[float, float] = (1.5, 4.0)

_INSTALLED = False
_ORIG_GETADDRINFO = None


def as_timeout(timeout) -> Tuple[float, float]:
    if isinstance(timeout, tuple) and len(timeout) == 2:
        return float(timeout[0]), float(timeout[1])
    try:
        read = float(timeout)
    except (TypeError, ValueError):
        read = DEFAULT_TIMEOUT[1]
    return DEFAULT_TIMEOUT[0], max(read, DEFAULT_TIMEOUT[0])


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    orig = _ORIG_GETADDRINFO or socket.getaddrinfo
    # Force AF_INET when caller asks for UNSPEC / 0
    fam = socket.AF_INET if family in (0, socket.AF_UNSPEC) else family
    return orig(host, port, fam, type, proto, flags)


def install_prefer_ipv4() -> bool:
    """Process-wide: make socket.getaddrinfo return IPv4 only. Idempotent.

    Call once at app startup so every requests/urllib3 path (including ones
    outside a prefer_ipv4() block) avoids black-holed IPv6.
    """
    global _INSTALLED, _ORIG_GETADDRINFO
    if _INSTALLED:
        return False
    _ORIG_GETADDRINFO = socket.getaddrinfo
    socket.getaddrinfo = _ipv4_only  # type: ignore[assignment]
    _INSTALLED = True
    return True


@contextmanager
def prefer_ipv4() -> Iterator[None]:
    """Temporarily make socket.getaddrinfo return IPv4 only.

    Nested use is safe. If install_prefer_ipv4() already ran, this is a no-op
    (process already prefers IPv4).
    """
    if _INSTALLED:
        yield
        return
    global _ORIG_GETADDRINFO
    orig = socket.getaddrinfo
    prev = _ORIG_GETADDRINFO
    _ORIG_GETADDRINFO = orig
    socket.getaddrinfo = _ipv4_only  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = orig  # type: ignore[assignment]
        _ORIG_GETADDRINFO = prev
