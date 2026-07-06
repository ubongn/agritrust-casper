"""x402 micropayment data-feed client.

The agent pays for every verification datum via the x402 protocol: HTTP 402 →
EIP-712 ``TransferWithAuthorization`` signature → facilitator verifies and
settles on Casper (CEP-18 ``transfer_with_authorization``) → data is released.

For cryptographic correctness we delegate the EIP-712 signing to the official
``@make-software/casper-x402`` Node client (``x402/client.js``) via subprocess.
This means the *real* protocol runs end-to-end — no reimplementation of Casper's
blake2b-based EIP-712 hashing and no mock of the 402 dance.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from .config import settings
from .models import DataSignal

console = Console()


def _run_node_client(url: str, max_cost: str) -> dict[str, Any]:
    """Invoke the Node x402 payer and return ``{"ok", "data" | "error", ...}``."""
    client = Path(settings.x402_node_client)
    if not client.exists():
        return {
            "ok": False,
            "error": f"node client not found at {client} (run the x402 server first)",
        }
    cmd = [
        "node",
        str(client),
        "fetch",
        url,
        "--max",
        max_cost,
        "--key",
        settings.agent_key_pem,
        "--asset",
        settings.x402_asset,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"ok": False, "error": "node is not installed / not on PATH"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[:500]}
    try:
        return {"ok": True, "data": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON response: {proc.stdout[:200]}"}


class DataFeedClient:
    """Pays-per-call to gather the off-chain signals an underwriter needs."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.x402_data_server).rstrip("/")

    def _pay(self, path: str) -> dict[str, Any] | None:
        url = f"{self.base_url}{path}"
        console.print(f"   [cyan]x402[/] → {url}")
        res = _run_node_client(url, settings.x402_max_cost_motes)
        if not res.get("ok"):
            console.print(f"   [yellow]x402 fallback[/] {res.get('error')}")
            return None
        data = res["data"].get("data", res["data"])
        cost = res["data"].get("costMotes", 0)
        console.print(f"   [green]paid[/] {cost} motes, got {len(data)} bytes")
        return {"data": data, "cost_motes": cost}

    def weather(self, lat: float, lon: float) -> DataSignal:
        res = self._pay(f"/weather?lat={lat}&lon={lon}")
        payload = res["data"] if res else {"source": "unavailable"}
        return DataSignal(feed="weather", payload=payload, cost_motes=res["cost_motes"] if res else 0)

    def price(self, crop: str) -> DataSignal:
        res = self._pay(f"/price?crop={crop}")
        payload = res["data"] if res else {"source": "unavailable"}
        return DataSignal(feed="price", payload=payload, cost_motes=res["cost_motes"] if res else 0)

    def kyc(self, farmer_id: str) -> DataSignal:
        res = self._pay(f"/kyc?farmer_id={farmer_id}")
        payload = res["data"] if res else {"source": "unavailable"}
        return DataSignal(feed="kyc", payload=payload, cost_motes=res["cost_motes"] if res else 0)

    def gather(self, invoice) -> list[DataSignal]:
        """Collect the full signal bundle for one invoice."""
        return [
            self.kyc(invoice.farmer.farmer_id),
            self.weather(invoice.farmer.latitude, invoice.farmer.longitude),
            self.price(invoice.crop.value),
        ]
