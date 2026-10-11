"""Orchestrates market bootstrap, sockets, reconnects and read-only snapshots."""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import websocket

from .config import Settings
from .constants import CANDLE_LIMIT, INTERVAL_MS, MARKETS
from .exchange import RESTClient, Stopped
from .indicators import Pair, crossed, initialize_pair, update_pair

LOG = logging.getLogger("difradar")
