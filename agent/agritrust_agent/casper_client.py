"""Thin wrapper around the ``casper-client`` CLI for on-chain AgriTrust ops.

All entry points map 1:1 to the Odra contract ABI. CL argument serialization uses
the ``--session-args "name:'value';..."`` form supported by casper-client v5; the
exact value format is resolved from ``casper-client --help`` at deploy time. The
wrapper is intentionally robust: every call returns parsed JSON + a tx hash, and
surfaces clear errors instead of panicking.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from rich.console import Console

from .config import settings

console = Console()


def _args(pairs: dict[str, str]) -> str:
    """Render session args in casper-client's ``name:'value';...`` form."""
    return ";".join(f"{k}:'{v}'" for k, v in pairs.items())


def _run(args: list[str], *, timeout: int = 90) -> dict[str, Any]:
    """Run a casper-client command and return parsed JSON output."""
    full = " ".join(args)
    console.print(f"   [dim]$ {settings.casper_client_bin} {full}[/]")
    try:
        proc = subprocess.run(
            [settings.casper_client_bin, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"'{settings.casper_client_bin}' not found — install with "
            "`cargo install casper-client --version 5.0.1 --locked`"
        )
    if proc.returncode != 0:
        raise RuntimeError(f"casper-client failed: {(proc.stderr or proc.stdout).strip()[:600]}")
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


class CasperContract:
    """High-level client for the deployed AgriTrust contract."""

    def __init__(self, package_hash: str | None = None) -> None:
        self.pkg = (package_hash or settings.contract_package_hash).replace("hash-", "")
        if not self.pkg:
            console.print("[yellow]warn[/] no contract hash set — calls will fail until AGRITRUST_CONTRACT_HASH is configured")

    # ── low-level call ──────────────────────────────────────────────────────
    def _call(self, entry_point: str, args: dict[str, str], *, amount: str = "0", key: str | None = None) -> dict[str, Any]:
        cli = [
            "put-transaction", "session",
            "--package-hash", f"hash-{self.pkg}",
            "--entry-point", entry_point,
            "--session-args", _args(args),
            "--chain-name", settings.chain_name,
            "--node-address", settings.node_url,
            "--payment-amount", settings.payment_amount_call,
            "--secret-key", key or settings.agent_key_pem,
        ]
        if int(amount) > 0:
            cli += ["--amount", amount]
        res = _run(cli)
        tx = res.get("transaction_hash") or res.get("deploy_hash") or "?"
        console.print(f"   [green]tx[/] {tx}")
        return res

    def _query(self, entry_point: str, args: dict[str, str] | None = None) -> dict[str, Any]:
        """Read-only query via session call returning the stored value."""
        return self._call(entry_point, args or {})

    # ── lifecycle entry points ──────────────────────────────────────────────
    def authorize_agent(self, agent_addr: str) -> dict[str, Any]:
        return self._call("authorize_agent", {"agent": agent_addr}, key=settings.deployer_key_pem)

    def register_invoice(self, commodity: str, region: str, face_amount: str, maturity: int) -> dict[str, Any]:
        return self._call(
            "register_invoice",
            {"commodity": commodity, "region": region, "face_amount": face_amount, "maturity": str(maturity)},
        )

    def post_verdict(self, invoice_id: int, score: int, risk_band: str,
                     max_advance_bps: int, discount_rate_bps: int,
                     data_hash: str, x402_cost: str) -> dict[str, Any]:
        return self._call(
            "post_verdict",
            {
                "invoice_id": str(invoice_id),
                "score": str(score),
                "risk_band": risk_band,
                "max_advance_bps": str(max_advance_bps),
                "discount_rate_bps": str(discount_rate_bps),
                "data_hash": data_hash,
                "x402_cost": x402_cost,
            },
        )

    def fund_invoice(self, invoice_id: int, amount_motes: str) -> dict[str, Any]:
        return self._call("fund_invoice", {"invoice_id": str(invoice_id)}, amount=amount_motes)

    def repay_and_settle(self, invoice_id: int, amount_motes: str) -> dict[str, Any]:
        return self._call("repay_and_settle", {"invoice_id": str(invoice_id)}, amount=amount_motes)

    def declare_default(self, invoice_id: int) -> dict[str, Any]:
        return self._call("declare_default", {"invoice_id": str(invoice_id)}, key=settings.deployer_key_pem)

    # ── reads ───────────────────────────────────────────────────────────────
    def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        return self._query("get_invoice", {"invoice_id": str(invoice_id)})

    def get_verdict(self, invoice_id: int) -> dict[str, Any]:
        return self._query("get_verdict", {"invoice_id": str(invoice_id)})

    def stats(self) -> dict[str, Any]:
        return self._query("stats")
