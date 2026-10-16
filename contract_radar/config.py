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
    # 0.165s keeps the startup burst within the owner-set 1920 weight/min target at
    # klines weight 5 (60/0.165*5 = 1818, +50 steady-state = 1868 <= 1920); the 2400
    # hard cap was met at 0.13s but the owner target was not (measured peak 2130).
    # Since v1.1 pacing is weight-based: 0.165s is the slot of one weight-5 request and
    # every request is charged by its weight, so all REST traffic, steady-state calls
    # included, stays within 60/0.165*5 = 1818 weight/min; weight-1 calls take 0.033s.
    request_spacing: float = 0.165
    # Binance reports the per-minute weight it has counted (X-MBX-USED-WEIGHT-1M);
    # at or above this value every REST call pauses until the next minute.
    weight_target: int = 1920
    # Optional directory for state snapshots (fast restart) and the signal event log.
    data_dir: str = ""
    # EMA21 急拉/急跌的服务端判定阈值（用于信号记录与评估；页面上的阈值只影响显示）。
    slope_mult: float = 3.0
    slope_min: float = 0.1
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    retry_attempts: int = 3
    resync_capacity: int = 1000
    http_workers: int = 32
    http_timeout: float = 10.0
    log_level: str = "INFO"
    log_file: str = ""
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")

    def __post_init__(self):
        if self.host not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("host 仅支持127.0.0.1或0.0.0.0")
        if self.market not in ("all", "um", "cm"):
            raise ValueError("market 必须为all/um/cm")
        limits = {
            "port": (1, 65535),
            "workers": (1, 32),
            "resync_workers": (1, 8),
            "ws_batch": (1, 200),
            "refresh_seconds": (60, 86400),
            "volume_refresh_seconds": (30, 300),
            "retry_attempts": (1, 5),
            "resync_capacity": (1, 10000),
            "http_workers": (1, 128),
            "weight_target": (100, 2400),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} 必须为{low}至{high}的整数")
        for name, low, high in (
            ("request_spacing", 0.01, 10),
            ("slope_mult", 0.5, 20),
            ("slope_min", 0.001, 5),
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
    parser = argparse.ArgumentParser(description="Binance Futures 5m EMA21 surge radar")
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
