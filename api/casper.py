"""Casper testnet client — JSON-RPC for reads + CLI for signed writes.

Reads go through the JSON-RPC API (httpx, no external binary needed).
Writes shell out to casper-client CLI (built in Docker, or pre-installed).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Optional

import httpx

# ── Config ──────────────────────────────────────────────────────────────────
RPC_URL = os.environ.get("CASPER_NODE_URL", "https://node.testnet.casper.network/rpc")
CHAIN_NAME = os.environ.get("CASPER_CHAIN_NAME", "casper-test")
CONTRACT_HASH = os.environ.get(
    "AGRITRUST_CONTRACT_HASH",
    "hash-c1dfe36ea24cac44224608ad69c880aedd0101cca405fbd686e461ac3d1bd29b",
)
DEPLOYER_KEY = os.environ.get("CASPER_DEPLOYER_KEY", "keys/deployer_secret_key.pem")
AGENT_KEY = os.environ.get("CASPER_AGENT_KEY", "keys/deployer_secret_key.pem")
CASPER_CLIENT_BIN = os.environ.get("CASPER_CLIENT_BIN", "casper-client")
PAYMENT_AMOUNT = os.environ.get("PAYMENT_CALL", "5000000000")
CSPR_LIVE_BASE = "https://testnet.cspr.live"


# ── JSON-RPC reads ──────────────────────────────────────────────────────────

def _rpc(method: str, params: dict | None = None) -> dict[str, Any]:
    """Call a Casper JSON-RPC method."""
    r = httpx.post(RPC_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {},
    }, timeout=30)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"RPC {method}: {data['error'].get('message', data['error'])}")
    return data.get("result", {})


def get_chain_height() -> int:
    """Get current block height."""
    return _rpc("info_get_status")["last_added_block_info"]["height"]


def get_contract_info() -> dict:
    """Query the deployed contract package + entry points."""
    height = get_chain_height()
    r = _rpc("query_global_state", {
        "state_identifier": {"BlockHeight": height},
        "key": CONTRACT_HASH,
        "path": [],
    })
    return r.get("stored_value", {}).get("ContractPackage", {})


def get_transaction(tx_hash: str) -> dict:
    """Get transaction details by hash."""
    if not tx_hash.startswith("transaction-"):
        tx_hash = f"transaction-{tx_hash}"
    return _rpc("info_get_transaction", {"transaction_hash": tx_hash})


def verify_tx_executed(tx_hash: str) -> bool:
    """Check if a transaction was executed on-chain."""
    try:
        result = get_transaction(tx_hash)
        exec_info = result.get("execution_info", {})
        return exec_info.get("execution_result", {}).get("Success") is not None
    except Exception:
        return False


# ── CLI writes (signed deploys) ──────────────────────────────────────────────

def _has_cli() -> bool:
    """Check if casper-client binary is available."""
    try:
        subprocess.run(
            [CASPER_CLIENT_BIN, "--version"],
            capture_output=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_cli(args: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run casper-client command and return parsed JSON."""
    proc = subprocess.run(
        [CASPER_CLIENT_BIN, *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"casper-client failed: {(proc.stderr or proc.stdout).strip()[:500]}"
        )
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def _call_entry_point(
    entry_point: str,
    args: dict[str, str],
    *,
    amount_motes: str = "0",
    key_file: str | None = None,
) -> dict[str, Any]:
    """Submit a signed deploy calling a stored contract entry point."""
    session_args = ";".join(f"{k}:'{v}'" for k, v in args.items())
    cli_args = [
        "put-transaction", "session",
        "--package-hash", CONTRACT_HASH,
        "--entry-point", entry_point,
        "--session-args", session_args,
        "--chain-name", CHAIN_NAME,
        "--node-address", RPC_URL,
        "--payment-amount", PAYMENT_AMOUNT,
        "--secret-key", key_file or AGENT_KEY,
    ]
    if int(amount_motes) > 0:
        cli_args += ["--amount", amount_motes]
    res = _run_cli(cli_args)
    tx_hash = res.get("transaction_hash") or res.get("deploy_hash") or ""
    return {"tx_hash": tx_hash, "raw": res}


# ── High-level lifecycle operations ─────────────────────────────────────────

def register_invoice(commodity: str, region: str,
                     face_amount_motes: str, maturity_ts: int) -> dict:
    """Register a new RWA invoice on-chain."""
    return _call_entry_point(
        "register_invoice",
        {
            "commodity": commodity,
            "region": region,
            "face_amount": face_amount_motes,
            "maturity": str(maturity_ts),
        },
        key_file=DEPLOYER_KEY,
    )


def post_verdict(invoice_id: int, score: int, risk_band: str,
                 max_advance_bps: int, discount_rate_bps: int,
                 data_hash: str, x402_cost_motes: str) -> dict:
    """Post an AI underwriting verdict on-chain."""
    return _call_entry_point(
        "post_verdict",
        {
            "invoice_id": str(invoice_id),
            "score": str(score),
            "risk_band": risk_band,
            "max_advance_bps": str(max_advance_bps),
            "discount_rate_bps": str(discount_rate_bps),
            "data_hash": data_hash,
            "x402_cost": x402_cost_motes,
        },
        key_file=AGENT_KEY,
    )


def fund_invoice(invoice_id: int, advance_motes: str) -> dict:
    """Fund an evaluated invoice as a liquidity provider."""
    return _call_entry_point(
        "fund_invoice",
        {"invoice_id": str(invoice_id), "advance_amount": advance_motes},
        amount_motes=advance_motes,
        key_file=DEPLOYER_KEY,
    )


def repay_and_settle(invoice_id: int, face_value_motes: str) -> dict:
    """Repay and settle a funded invoice."""
    return _call_entry_point(
        "repay_and_settle",
        {"invoice_id": str(invoice_id)},
        amount_motes=face_value_motes,
        key_file=DEPLOYER_KEY,
    )


def tx_url(tx_hash: str) -> str:
    """Build a cspr.live URL for a transaction."""
    clean = tx_hash.replace("transaction-", "").replace("deploy-", "")
    return f"{CSPR_LIVE_BASE}/transactions/{clean}"
