"""Centralized configuration for the AgriTrust underwriting agent.

All values are read from environment variables with sensible defaults so the
agent runs out-of-the-box against the Casper Testnet. Secrets (private keys,
LLM API keys, CSPR.cloud tokens) are NEVER hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    # ── Casper network ──────────────────────────────────────────────────────
    chain_name: str = field(default_factory=lambda: _env("CASPER_CHAIN_NAME", "casper-test"))
    node_url: str = field(
        default_factory=lambda: _env("CASPER_NODE_URL", "https://node.testnet.casper.network/rpc")
    )
    # CSPR.cloud indexed API (optional; used by the MCP-style read layer)
    cspr_cloud_url: str = field(
        default_factory=lambda: _env("CSPR_CLOUD_URL", "https://api.testnet.cspr.cloud")
    )
    cspr_cloud_key: str = field(default_factory=lambda: _env("CSPR_CLOUD_API_KEY"))

    # Deployer / owner key (PEM on disk)
    deployer_key_pem: str = field(default_factory=lambda: _env("CASPER_DEPLOYER_KEY", "keys/secret_key.pem"))
    # The agent's own key (authorized to post verdicts)
    agent_key_pem: str = field(default_factory=lambda: _env("CASPER_AGENT_KEY", "keys/agent_secret_key.pem"))

    # Deployed AgriTrust contract package hash, e.g. hash-<64hex>
    contract_package_hash: str = field(default_factory=lambda: _env("AGRITRUST_CONTRACT_HASH"))

    # ── x402 data-feed micropayments ────────────────────────────────────────
    # The base URL of the x402-protected data-feed server the agent pays per call.
    x402_data_server: str = field(default_factory=lambda: _env("X402_DATA_SERVER", "http://localhost:8402"))
    # Path to the Node x402 payer client (signs EIP-712 authorizations).
    x402_node_client: str = field(default_factory=lambda: _env("X402_NODE_CLIENT", "../x402/client.js"))
    # CEP-18 token used as the x402 settlement asset (contract package hash, 64 hex)
    x402_asset: str = field(default_factory=lambda: _env("X402_ASSET"))
    x402_max_cost_motes: str = field(default_factory=lambda: _env("X402_MAX_COST_MOTES", "5000000"))

    # ── LLM underwriting (optional) ─────────────────────────────────────────
    llm_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))

    # ── Operational ─────────────────────────────────────────────────────────
    casper_client_bin: str = field(default_factory=lambda: _env("CASPER_CLIENT_BIN", "casper-client"))
    payment_amount_install: str = field(default_factory=lambda: _env("PAYMENT_INSTALL", "300000000000"))
    payment_amount_call: str = field(default_factory=lambda: _env("PAYMENT_CALL", "5000000000"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def has_contract(self) -> bool:
        return bool(self.contract_package_hash)


settings = Settings()
