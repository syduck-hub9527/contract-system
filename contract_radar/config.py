"""Validated runtime configuration: defaults < environment < command line."""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    market: str = "all"
    workers: int = 7
    resync_workers: int = 2
    ws_batch: int = 75
    refresh_seconds: int = 3600
    volume_refresh_seconds: int = 60
    # 0.13s keeps the startup burst under 2400 weight/min at klines weight 5
    # (60/0.13*5 = 2307); see spec 5.3 and the v4 CANDLE_LIMIT increase.
    request_spacing: float = 0.13
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    retry_attempts: int = 3
    resync_capacity: int = 1000
    http_workers: int = 32
    http_timeout: float = 10.0
    log_level: str = "INFO"
    log_file: str = ""
    signals_enabled: int = 0  # M1/M2 research only; enable after G1 owner review.
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")

    def __post_init__(self):
        if self.host not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("host 仅支持127.0.0.1或0.0.0.0")
        if self.market not in ("all", "um", "cm"):
            raise ValueError("market 必须为all/um/cm")
        limits = {
            "signals_enabled": (0, 1),
            "port": (1, 65535),
            "workers": (1, 32),
            "resync_workers": (1, 8),
            "ws_batch": (1, 200),
            "refresh_seconds": (60, 86400),
            "volume_refresh_seconds": (30, 300),
            "retry_attempts": (1, 5),
            "resync_capacity": (1, 10000),
            "http_workers": (1, 128),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} 必须为{low}至{high}的整数")
        for name, low, high in (
            ("request_spacing", 0.01, 10),
            ("connect_timeout", 0.1, 30),
            ("read_timeout", 0.1, 60),
            ("http_timeout", 0.1, 60),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} 必须在{low}至{high}之间")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("log_level 无效")
        if not self.allowed_hosts or any(
            not h or any(c in h for c in "/@\\\r\n\t ") or h == "*" for h in self.allowed_hosts
        ):
            raise ValueError("allowed_hosts 必须是显式主机名，不能使用通配符或URL")

    @property
    def markets(self):
        return {"all": ("UM", "CM"), "um": ("UM",), "cm": ("CM",)}[self.market]


def parse_settings(argv=None, environ=None):
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Binance Futures 5m DIF live radar")
    defaults = Settings()
    converters = {}
    for name in Settings.__dataclass_fields__:
        if name == "allowed_hosts":

            def converter(s):
                return tuple(v.strip().lower() for v in s.split(","))
        else:
            converter = type(getattr(defaults, name))
        converters[name] = converter
        parser.add_argument("--" + name.replace("_", "-"), type=converter, default=argparse.SUPPRESS)
    explicit = vars(parser.parse_args(argv))
    values = {}
    for name in Settings.__dataclass_fields__:
        if name in explicit:
            values[name] = explicit[name]
        elif "RADAR_" + name.upper() in env:
            try:
                values[name] = converters[name](env["RADAR_" + name.upper()])
            except (ValueError, TypeError):
                parser.error(f"RADAR_{name.upper()} 格式无效")
        else:
            values[name] = getattr(defaults, name)
    try:
        return Settings(**values)
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
