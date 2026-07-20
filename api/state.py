"""State store for AgriTrust demo backend.

Uses PostgreSQL when DATABASE_URL is set (production on Render).
Falls back to in-memory store when unset (local dev / no DB configured).

Both backends expose the identical StateStore interface, so main.py is
agnostic to the underlying persistence layer.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from enum import IntEnum
from typing import Optional


class InvoiceStatus(IntEnum):
    REGISTERED = 0
    EVALUATED = 1
    FUNDED = 2
    SETTLED = 3
    DEFAULTED = 4


STATUS_LABELS = {0: "REGISTERED", 1: "EVALUATED", 2: "FUNDED", 3: "SETTLED", 4: "DEFAULTED"}
STATUS_COLORS = {0: "#60a5fa", 1: "#fbbf24", 2: "#4ade80", 3: "#22c55e", 4: "#f87171"}


@dataclass
class RiskVerdict:
    invoice_id: int
    score: int
    risk_band: str
    max_advance_bps: int
    discount_rate_bps: int
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
    status: str
    cspr_live_url: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL backend
# ──────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")


class PostgresStore:
    """PostgreSQL-backed store. Data survives process restarts."""

    def __init__(self, url: str):
        import psycopg2
        from psycopg2.pool import SimpleConnectionPool
        # Render may provide URL as postgres:// — psycopg2 needs postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        self.pool = SimpleConnectionPool(1, 5, url)
        self._init_db()

    def _conn(self):
        return self.pool.getconn()

    def _put(self, conn):
        self.pool.putconn(conn)

    def _init_db(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS invoices (
                        id SERIAL PRIMARY KEY,
                        farmer_address TEXT NOT NULL,
                        commodity TEXT NOT NULL,
                        region TEXT NOT NULL,
                        face_amount_motes TEXT NOT NULL,
                        maturity BIGINT NOT NULL,
                        status INT NOT NULL DEFAULT 0,
                        advance_amount_motes TEXT NOT NULL DEFAULT '0',
                        funder_address TEXT NOT NULL DEFAULT '',
                        created_at BIGINT NOT NULL,
                        tx_hash TEXT NOT NULL DEFAULT ''
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS verdicts (
                        invoice_id INT PRIMARY KEY REFERENCES invoices(id),
                        score INT NOT NULL,
                        risk_band TEXT NOT NULL,
                        max_advance_bps INT NOT NULL,
                        discount_rate_bps INT NOT NULL,
                        data_hash TEXT NOT NULL,
                        x402_cost_motes BIGINT NOT NULL,
                        agent_address TEXT NOT NULL,
                        timestamp BIGINT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        tx_hash TEXT NOT NULL,
                        entry_point TEXT NOT NULL,
                        invoice_id INT,
                        amount_motes TEXT NOT NULL,
                        timestamp BIGINT NOT NULL,
                        status TEXT NOT NULL,
                        cspr_live_url TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.commit()
            # Seed if empty
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM invoices")
                if cur.fetchone()[0] == 0:
                    self._seed(conn)
        finally:
            self._put(conn)

    def _seed(self, conn):
        """Seed qualification-round demo data."""
        base_ts = 1751232000
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO invoices (id, farmer_address, commodity, region,
                    face_amount_motes, maturity, status, advance_amount_motes,
                    funder_address, created_at, tx_hash)
                VALUES (1, 'hash-deployer', 'maize', 'Ashanti, Ghana',
                    '4500000000', %s, 3, '2700000000',
                    'hash-deployer', %s, 'qualification-round')
            """, (base_ts + 7776000, base_ts))
            cur.execute("""
                INSERT INTO verdicts (invoice_id, score, risk_band, max_advance_bps,
                    discount_rate_bps, data_hash, x402_cost_motes, agent_address, timestamp)
                VALUES (1, 720, 'LOW', 6000, 1200,
                    'a]b3f7e2c1d8...k402_verify', 850000, 'hash-agent', %s)
            """, (base_ts + 3600,))
            for ep, inv_id, amt, ts in [
                ("init", None, "0", base_ts - 600),
                ("authorize_agent", None, "0", base_ts - 300),
                ("register_invoice", 1, "0", base_ts),
                ("post_verdict", 1, "0", base_ts + 3600),
                ("fund_invoice", 1, "2700000000", base_ts + 7200),
                ("repay_and_settle", 1, "4500000000", base_ts + 7776000),
            ]:
                cur.execute("""
                    INSERT INTO transactions (tx_hash, entry_point, invoice_id,
                        amount_motes, timestamp, status, cspr_live_url)
                    VALUES (%s, %s, %s, %s, %s, 'executed',
                        'https://testnet.cspr.live/transactions/qualification-round')
                """, (f"qualification-round-{ep}", ep, inv_id, amt, ts))
            conn.commit()

    def _row_to_invoice(self, row) -> Invoice:
        (rid, farmer_addr, commodity, region, face, maturity, status,
         advance, funder, created, tx_hash) = row
        inv = Invoice(
            id=rid, farmer_address=farmer_addr, commodity=commodity, region=region,
            face_amount_motes=face, maturity=maturity, status=status,
            advance_amount_motes=advance, funder_address=funder,
            created_at=created, tx_hash=tx_hash,
        )
        # Load verdict if exists
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT score, risk_band, max_advance_bps, discount_rate_bps, data_hash, x402_cost_motes, agent_address, timestamp FROM verdicts WHERE invoice_id = %s", (rid,))
                vrow = cur.fetchone()
                if vrow:
                    inv.verdict = RiskVerdict(
                        invoice_id=rid, score=vrow[0], risk_band=vrow[1],
                        max_advance_bps=vrow[2], discount_rate_bps=vrow[3],
                        data_hash=vrow[4], x402_cost_motes=vrow[5],
                        agent_address=vrow[6], timestamp=vrow[7],
                    )
        finally:
            self._put(conn)
        return inv

    def create_invoice(self, *, commodity, region, face_amount_motes, maturity,
                       farmer_address, tx_hash) -> Invoice:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO invoices (farmer_address, commodity, region,
                        face_amount_motes, maturity, status, advance_amount_motes,
                        funder_address, created_at, tx_hash)
                    VALUES (%s, %s, %s, %s, %s, 0, '0', '', %s, %s)
                    RETURNING id
                """, (farmer_address, commodity, region, face_amount_motes,
                      maturity, int(time.time()), tx_hash))
                new_id = cur.fetchone()[0]
                conn.commit()
            return self.get_invoice(new_id)
        finally:
            self._put(conn)

    def add_verdict(self, invoice_id: int, verdict: RiskVerdict, tx_hash: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO verdicts (invoice_id, score, risk_band, max_advance_bps,
                        discount_rate_bps, data_hash, x402_cost_motes, agent_address, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (invoice_id) DO UPDATE SET
                        score=EXCLUDED.score, risk_band=EXCLUDED.risk_band,
                        max_advance_bps=EXCLUDED.max_advance_bps,
                        discount_rate_bps=EXCLUDED.discount_rate_bps,
                        data_hash=EXCLUDED.data_hash,
                        x402_cost_motes=EXCLUDED.x402_cost_motes,
                        agent_address=EXCLUDED.agent_address,
                        timestamp=EXCLUDED.timestamp
                """, (invoice_id, verdict.score, verdict.risk_band,
                      verdict.max_advance_bps, verdict.discount_rate_bps,
                      verdict.data_hash, verdict.x402_cost_motes,
                      verdict.agent_address, verdict.timestamp))
                cur.execute("""
                    UPDATE invoices SET status = GREATEST(status, 1), tx_hash = %s
                    WHERE id = %s
                """, (tx_hash, invoice_id))
                conn.commit()
        finally:
            self._put(conn)

    def fund_invoice(self, invoice_id: int, advance_motes: str,
                     funder_address: str, tx_hash: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE invoices SET advance_amount_motes = %s,
                        funder_address = %s, status = 2, tx_hash = %s
                    WHERE id = %s
                """, (advance_motes, funder_address, tx_hash, invoice_id))
                conn.commit()
        finally:
            self._put(conn)

    def settle_invoice(self, invoice_id: int, tx_hash: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE invoices SET status = 3, tx_hash = %s WHERE id = %s
                """, (tx_hash, invoice_id))
                conn.commit()
        finally:
            self._put(conn)

    def add_transaction(self, tx: Transaction):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO transactions (tx_hash, entry_point, invoice_id,
                        amount_motes, timestamp, status, cspr_live_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (tx.tx_hash, tx.entry_point, tx.invoice_id,
                      tx.amount_motes, tx.timestamp, tx.status, tx.cspr_live_url))
                conn.commit()
        finally:
            self._put(conn)

    def get_invoice(self, invoice_id: int) -> Optional[Invoice]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, farmer_address, commodity, region, face_amount_motes,
                        maturity, status, advance_amount_motes, funder_address,
                        created_at, tx_hash
                    FROM invoices WHERE id = %s
                """, (invoice_id,))
                row = cur.fetchone()
                if not row:
                    return None
            return self._row_to_invoice(row)
        finally:
            self._put(conn)

    def list_invoices(self, status: Optional[int] = None) -> list[Invoice]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if status is not None:
                    cur.execute("""
                        SELECT id, farmer_address, commodity, region, face_amount_motes,
                            maturity, status, advance_amount_motes, funder_address,
                            created_at, tx_hash
                        FROM invoices WHERE status = %s ORDER BY id DESC
                    """, (status,))
                else:
                    cur.execute("""
                        SELECT id, farmer_address, commodity, region, face_amount_motes,
                            maturity, status, advance_amount_motes, funder_address,
                            created_at, tx_hash
                        FROM invoices ORDER BY id DESC
                    """)
                rows = cur.fetchall()
            return [self._row_to_invoice(r) for r in rows]
        finally:
            self._put(conn)

    def get_stats(self) -> dict:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM invoices")
                total_invoices = cur.fetchone()[0]
                cur.execute("SELECT COALESCE(SUM(advance_amount_motes::bigint), 0) FROM invoices WHERE status >= 2")
                total_funded = str(cur.fetchone()[0])
                cur.execute("SELECT COALESCE(SUM(face_amount_motes::bigint), 0) FROM invoices WHERE status >= 3")
                total_settled = str(cur.fetchone()[0])
                cur.execute("SELECT COALESCE(SUM(x402_cost_motes), 0) FROM verdicts")
                total_x402 = str(cur.fetchone()[0])
            return {
                "total_invoices": total_invoices,
                "total_funded_motes": total_funded,
                "total_settled_motes": total_settled,
                "total_defaulted_motes": "0",
                "total_x402_spent_motes": total_x402,
                "active_agents": 1,
            }
        finally:
            self._put(conn)

    def get_transactions(self, limit: int = 20) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT tx_hash, entry_point, invoice_id, amount_motes,
                        timestamp, status, cspr_live_url
                    FROM transactions ORDER BY id DESC LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
            return [
                {"tx_hash": r[0], "entry_point": r[1], "invoice_id": r[2],
                 "amount_motes": r[3], "timestamp": r[4], "status": r[5],
                 "cspr_live_url": r[6]}
                for r in rows
            ]
        finally:
            self._put(conn)


# ──────────────────────────────────────────────────────────────────────────────
# In-memory fallback (local dev without DATABASE_URL)
# ──────────────────────────────────────────────────────────────────────────────

class MemoryStore:
    """Thread-safe in-memory state."""

    def __init__(self):
        self.invoices: dict[int, Invoice] = {}
        self.transactions: list[Transaction] = []
        self._next_id = 1
        self._seed_existing()

    def _seed_existing(self):
        base_ts = 1751232000
        inv1 = Invoice(
            id=1, farmer_address="hash-deployer",
            commodity="maize", region="Ashanti, Ghana",
            face_amount_motes="4500000000", maturity=base_ts + 7776000,
            status=3, advance_amount_motes="2700000000",
            funder_address="hash-deployer", created_at=base_ts,
            verdict=RiskVerdict(
                invoice_id=1, score=720, risk_band="LOW",
                max_advance_bps=6000, discount_rate_bps=1200,
                data_hash="a]b3f7e2c1d8...k402_verify",
                x402_cost_motes=850000, agent_address="hash-agent",
                timestamp=base_ts + 3600,
            ),
            tx_hash="qualification-round",
        )
        self.invoices[1] = inv1
        self._next_id = 2
        for ep, inv_id, amt, ts in [
            ("init", None, "0", base_ts - 600),
            ("authorize_agent", None, "0", base_ts - 300),
            ("register_invoice", 1, "0", base_ts),
            ("post_verdict", 1, "0", base_ts + 3600),
            ("fund_invoice", 1, "2700000000", base_ts + 7200),
            ("repay_and_settle", 1, "4500000000", base_ts + 7776000),
        ]:
            self.transactions.insert(0, Transaction(
                tx_hash=f"qualification-round-{ep}", entry_point=ep,
                invoice_id=inv_id, amount_motes=amt, timestamp=ts,
                status="executed",
                cspr_live_url="https://testnet.cspr.live/transactions/qualification-round",
            ))

    def create_invoice(self, *, commodity, region, face_amount_motes, maturity,
                       farmer_address, tx_hash) -> Invoice:
        inv_id = self._next_id
        self._next_id += 1
        inv = Invoice(id=inv_id, farmer_address=farmer_address, commodity=commodity,
                      region=region, face_amount_motes=face_amount_motes,
                      maturity=maturity, created_at=int(time.time()), tx_hash=tx_hash)
        self.invoices[inv_id] = inv
        return inv

    def add_verdict(self, invoice_id: int, verdict: RiskVerdict, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.verdict = verdict
            inv.status = max(inv.status, InvoiceStatus.EVALUATED)
            inv.tx_hash = tx_hash

    def fund_invoice(self, invoice_id: int, advance_motes: str,
                     funder_address: str, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.advance_amount_motes = advance_motes
            inv.funder_address = funder_address
            inv.status = InvoiceStatus.FUNDED
            inv.tx_hash = tx_hash

    def settle_invoice(self, invoice_id: int, tx_hash: str):
        inv = self.invoices.get(invoice_id)
        if inv:
            inv.status = InvoiceStatus.SETTLED
            inv.tx_hash = tx_hash

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
        funded = sum(int(i.advance_amount_motes) for i in self.invoices.values() if i.status >= 2)
        settled = sum(int(i.face_amount_motes) for i in self.invoices.values() if i.status >= 3)
        x402 = sum(i.verdict.x402_cost_motes for i in self.invoices.values() if i.verdict)
        return {
            "total_invoices": len(self.invoices),
            "total_funded_motes": str(funded),
            "total_settled_motes": str(settled),
            "total_defaulted_motes": "0",
            "total_x402_spent_motes": str(x402),
            "active_agents": 1,
        }

    def get_transactions(self, limit: int = 20) -> list[dict]:
        return [asdict(t) for t in self.transactions[:limit]]


# ──────────────────────────────────────────────────────────────────────────────
# Singleton — picks backend based on DATABASE_URL
# ──────────────────────────────────────────────────────────────────────────────

if DATABASE_URL:
    try:
        store = PostgresStore(DATABASE_URL)
        store_backend = "postgresql"
    except Exception as e:
        print(f"[state] WARNING: PostgreSQL connection failed ({e}), falling back to memory")
        store = MemoryStore()
        store_backend = "memory"
else:
    store = MemoryStore()
    store_backend = "memory"
