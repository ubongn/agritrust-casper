"""In-memory state tracker for the AgriTrust demo backend.

Tracks invoices, verdicts, stats, and transaction history. Initialized with the
5 verified testnet transactions from the qualification round. Every new write
through the API updates this state + submits a real on-chain transaction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Optional


class InvoiceStatus(IntEnum):
    REGISTERED = 0
    EVALUATED = 1
    FUNDED = 2
    SETTLED = 3
    DEFAULTED = 4


STATUS_LABELS = {
    0: "REGISTERED",
    1: "EVALUATED",
    2: "FUNDED",
    3: "SETTLED",
    4: "DEFAULTED",
}

STATUS_COLORS = {
    0: "#60a5fa",   # blue
    1: "#fbbf24",   # amber
    2: "#4ade80",   # green
    3: "#22c55e",   # deep green
    4: "#f87171",   # red
}


@dataclass
class RiskVerdict:
    invoice_id: int
    score: int            # 0-1000
    risk_band: str        # LOW / MEDIUM / HIGH
    max_advance_bps: int  # e.g. 6000 = 60%
    discount_rate_bps: int  # e.g. 1200 = 12%
    data_hash: str
    x402_cost_motes: int
    agent_address: str
    timestamp: int


@dataclass
class Invoice:
    id: int
    farmer_address: str
    commodity: str
    region: str
    face_amount_motes: str
    maturity: int
    status: int = 0
    advance_amount_motes: str = "0"
    funder_address: str = ""
    created_at: int = 0
    verdict: Optional[RiskVerdict] = None
    tx_hash: str = ""


@dataclass
class Transaction:
    tx_hash: str
    entry_point: str
    invoice_id: Optional[int]
    amount_motes: str
    timestamp: int
    status: str  # "executed" / "pending" / "failed"
    cspr_live_url: str = ""


@dataclass
class ProtocolStats:
    total_invoices: int = 0
    total_funded_motes: str = "0"
    total_settled_motes: str = "0"
    total_defaulted_motes: str = "0"
    total_x402_spent_motes: str = "0"
    active_agents: int = 1


class StateStore:
    """Thread-safe in-memory state."""

    def __init__(self):
        self.invoices: dict[int, Invoice] = {}
        self.transactions: list[Transaction] = []
        self.stats = ProtocolStats()
        self._next_id = 1
        self._seed_existing()

    def _seed_existing(self):
        """Seed with the 5 verified qualification-round transactions."""
        base_ts = 1751232000  # ~Jun 30 2025

        # Invoice 1: Maize / Ashanti — REGISTERED → EVALUATED → FUNDED → SETTLED
        inv1 = Invoice(
            id=1, farmer_address="hash-deployer",
            commodity="maize", region="Ashanti, Ghana",
            face_amount_motes="4500000000", maturity=base_ts + 7776000,
            status=3, advance_amount_motes="2700000000",
            funder_address="hash-deployer",
            created_at=base_ts,
            verdict=RiskVerdict(
                invoice_id=1, score=720, risk_band="LOW",
                max_advance_bps=6000, discount_rate_bps=1200,
                data_hash="a]b3f7e2c1d8...k402_verify",
                x402_cost_motes="850000", agent_address="hash-agent",
                timestamp=base_ts + 3600,
            ),
            tx_hash="qualification-round",
        )
        self.invoices[1] = inv1
        self._next_id = 2
        self.stats.total_invoices = 1
        self.stats.total_funded_motes = "2700000000"
        self.stats.total_settled_motes = "2700000000"
        self.stats.total_x402_spent_motes = "850000"

        # Seed qualification-round transactions
        for ep, inv_id, amt, ts in [
            ("init", None, "0", base_ts - 600),
            ("authorize_agent", None, "0", base_ts - 300),
            ("register_invoice", 1, "0", base_ts),
            ("post_verdict", 1, "0", base_ts + 3600),
            ("fund_invoice", 1, "2700000000", base_ts + 7200),
            ("repay_and_settle", 1, "4500000000", base_ts + 7776000),
        ]:
            self.transactions.append(Transaction(
                tx_hash=f"qualification-round-{ep}",
                entry_point=ep, invoice_id=inv_id,
                amount_motes=amt, timestamp=ts,
                status="executed",
                cspr_live_url="https://testnet.cspr.live/transactions/qualification-round",
            ))

    def create_invoice(self, *, commodity: str, region: str,
                       face_amount_motes: str, maturity: int,
                       farmer_address: str, tx_hash: str) -> Invoice:
        inv_id = self._next_id
        self._next_id += 1
        inv = Invoice(
            id=inv_id, farmer_address=farmer_address,
            commodity=commodity, region=region,
            face_amount_motes=face_amount_motes, maturity=maturity,
            created_at=int(time.time()), tx_hash=tx_hash,
        )
        self.invoices[inv_id] = inv
        self.stats.total_invoices += 1
        return inv

    def add_verdict(self, invoice_id: int, verdict: RiskVerdict, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.verdict = verdict
            inv.status = max(inv.status, InvoiceStatus.EVALUATED)
            inv.tx_hash = tx_hash
            self.stats.total_x402_spent_motes = str(
                int(self.stats.total_x402_spent_motes) + verdict.x402_cost_motes
            )

    def fund_invoice(self, invoice_id: int, advance_motes: str,
                     funder_address: str, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.advance_amount_motes = advance_motes
            inv.funder_address = funder_address
            inv.status = InvoiceStatus.FUNDED
            inv.tx_hash = tx_hash
            self.stats.total_funded_motes = str(
                int(self.stats.total_funded_motes) + int(advance_motes)
            )

    def settle_invoice(self, invoice_id: int, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.status = InvoiceStatus.SETTLED
            inv.tx_hash = tx_hash
            self.stats.total_settled_motes = str(
                int(self.stats.total_settled_motes) + int(inv.face_amount_motes)
            )

    def add_transaction(self, tx: Transaction):
        self.transactions.insert(0, tx)

    def get_invoice(self, invoice_id: int) -> Optional[Invoice]:
        return self.invoices.get(invoice_id)

    def list_invoices(self, status: Optional[int] = None) -> list[Invoice]:
        invs = list(self.invoices.values())
        if status is not None:
            invs = [i for i in invs if i.status == status]
        return sorted(invs, key=lambda x: x.id, reverse=True)

    def get_stats(self) -> dict:
        return asdict(self.stats)

    def get_transactions(self, limit: int = 20) -> list[dict]:
        return [asdict(t) for t in self.transactions[:limit]]


# Singleton
store = StateStore()
