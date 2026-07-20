"""AgriTrust API — FastAPI backend for the interactive dApp.

Endpoints:
  GET  /api/health               — health check
  GET  /api/stats                — protocol statistics
  GET  /api/invoices             — list invoices (filter by status)
  GET  /api/invoice/{id}         — get invoice detail
  POST /api/invoice/register     — farmer registers a new invoice
  POST /api/invoice/{id}/evaluate — AI agent evaluates (x402 + scoring)
  POST /api/invoice/{id}/fund    — LP funds an evaluated invoice
  POST /api/invoice/{id}/settle  — farmer repays & settles
  GET  /api/transactions         — recent on-chain transactions
  GET  /api/chain/status         — Casper testnet status
"""
from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .state import store, store_backend, InvoiceStatus, STATUS_LABELS, STATUS_COLORS, RiskVerdict
from . import casper
from .underwriter import evaluate_invoice

app = FastAPI(
    title="AgriTrust API",
    description="Autonomous trade-finance for emerging-market farmers — Casper Network",
    version="1.0.0",
)

# CORS — allow the Vercel frontend + localhost dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://agritrust-casper.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request models ──────────────────────────────────────────────────────────

class RegisterInvoiceReq(BaseModel):
    commodity: str = Field(..., example="maize")
    region: str = Field(..., example="Ashanti, Ghana")
    face_amount_cspr: float = Field(..., gt=0, description="Face value in CSPR")
    maturity_days: int = Field(..., ge=7, le=365, description="Days until maturity")


class FundInvoiceReq(BaseModel):
    advance_ratio: Optional[float] = Field(
        None, ge=0.1, le=1.0,
        description="Fraction of face value to advance (defaults to verdict max)",
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _motes_from_cspr(cspr: float) -> str:
    """Convert CSPR to motes (1 CSPR = 1e9 motes)."""
    return str(int(cspr * 1_000_000_000))


def _cspr_from_motes(motes: str | int) -> float:
    return int(motes) / 1_000_000_000


def _invoice_dict(inv) -> dict:
    """Serialize an invoice to dict for API response."""
    d = {
        "id": inv.id,
        "farmer_address": inv.farmer_address,
        "commodity": inv.commodity,
        "region": inv.region,
        "face_amount_cspr": _cspr_from_motes(inv.face_amount_motes),
        "face_amount_motes": inv.face_amount_motes,
        "maturity": inv.maturity,
        "maturity_date": time.strftime("%Y-%m-%d", time.gmtime(inv.maturity)) if inv.maturity else "",
        "status": inv.status,
        "status_label": STATUS_LABELS.get(inv.status, "UNKNOWN"),
        "status_color": STATUS_COLORS.get(inv.status, "#888"),
        "advance_amount_cspr": _cspr_from_motes(inv.advance_amount_motes) if inv.advance_amount_motes != "0" else 0,
        "funder_address": inv.funder_address,
        "created_at": inv.created_at,
        "created_at_date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(inv.created_at)),
        "tx_hash": inv.tx_hash,
        "tx_url": casper.tx_url(inv.tx_hash) if inv.tx_hash and inv.tx_hash != "qualification-round" else "",
    }
    if inv.verdict:
        d["verdict"] = {
            "score": inv.verdict.score,
            "risk_band": inv.verdict.risk_band,
            "max_advance_bps": inv.verdict.max_advance_bps,
            "max_advance_pct": inv.verdict.max_advance_bps / 100,
            "discount_rate_bps": inv.verdict.discount_rate_bps,
            "discount_rate_pct": inv.verdict.discount_rate_bps / 100,
            "data_hash": inv.verdict.data_hash,
            "x402_cost_cspr": _cspr_from_motes(inv.verdict.x402_cost_motes),
            "agent_address": inv.verdict.agent_address,
        }
    return d


CLI_AVAILABLE = casper._has_cli()


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "contract": casper.CONTRACT_HASH[:24] + "...",
        "cli_available": CLI_AVAILABLE,
        "chain": casper.CHAIN_NAME,
        "backend": store_backend,
    }


@app.get("/api/stats")
async def get_stats():
    stats = store.get_stats()
    # Add derived values
    stats["total_funded_cspr"] = _cspr_from_motes(stats["total_funded_motes"])
    stats["total_settled_cspr"] = _cspr_from_motes(stats["total_settled_motes"])
    stats["total_x402_spent_cspr"] = _cspr_from_motes(stats["total_x402_spent_motes"])
    return stats


@app.get("/api/invoices")
async def list_invoices(
    status: Optional[int] = Query(None, ge=0, le=4, description="Filter by status"),
):
    invs = store.list_invoices(status=status)
    return {"invoices": [_invoice_dict(i) for i in invs], "count": len(invs)}


@app.get("/api/invoice/{invoice_id}")
async def get_invoice(invoice_id: int):
    inv = store.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return _invoice_dict(inv)


@app.post("/api/invoice/register")
async def register_invoice(req: RegisterInvoiceReq):
    """Farmer registers a new RWA invoice. Submits real on-chain transaction."""
    face_motes = _motes_from_cspr(req.face_amount_cspr)
    maturity_ts = int(time.time()) + req.maturity_days * 86400

    tx_hash = "demo-tx"
    if CLI_AVAILABLE:
        try:
            result = casper.register_invoice(
                commodity=req.commodity,
                region=req.region,
                face_amount_motes=face_motes,
                maturity_ts=maturity_ts,
            )
            tx_hash = result.get("tx_hash", "error")
        except Exception as e:
            raise HTTPException(500, f"On-chain tx failed: {str(e)[:200]}")

    inv = store.create_invoice(
        commodity=req.commodity,
        region=req.region,
        face_amount_motes=face_motes,
        maturity=maturity_ts,
        farmer_address="hash-deployer",
        tx_hash=tx_hash,
    )

    store.add_transaction(_make_tx(tx_hash, "register_invoice", inv.id, "0"))

    return {
        "ok": True,
        "invoice": _invoice_dict(inv),
        "tx_hash": tx_hash,
        "tx_url": casper.tx_url(tx_hash) if tx_hash != "demo-tx" else "",
        "message": f"Invoice #{inv.id} registered on Casper Testnet. AI evaluation starting...",
    }


@app.post("/api/invoice/{invoice_id}/evaluate")
async def evaluate(invoice_id: int):
    """AI agent evaluates an invoice: x402 data acquisition + risk scoring + on-chain verdict."""
    inv = store.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status >= InvoiceStatus.EVALUATED:
        raise HTTPException(400, f"Invoice already evaluated (status: {STATUS_LABELS[inv.status]})")

    # Run the underwriting model
    verdict_data = evaluate_invoice(
        invoice_id, inv.commodity, inv.region, inv.face_amount_motes
    )

    tx_hash = "demo-tx"
    if CLI_AVAILABLE:
        try:
            result = casper.post_verdict(
                invoice_id=invoice_id,
                score=verdict_data["score"],
                risk_band=verdict_data["risk_band"],
                max_advance_bps=verdict_data["max_advance_bps"],
                discount_rate_bps=verdict_data["discount_rate_bps"],
                data_hash=verdict_data["data_hash"],
                x402_cost_motes=verdict_data["x402_cost_motes"],
            )
            tx_hash = result.get("tx_hash", "error")
        except Exception as e:
            raise HTTPException(500, f"On-chain verdict failed: {str(e)[:200]}")

    # Update state
    verdict = RiskVerdict(
        invoice_id=invoice_id,
        score=verdict_data["score"],
        risk_band=verdict_data["risk_band"],
        max_advance_bps=verdict_data["max_advance_bps"],
        discount_rate_bps=verdict_data["discount_rate_bps"],
        data_hash=verdict_data["data_hash"],
        x402_cost_motes=int(verdict_data["x402_cost_motes"]),
        agent_address="hash-agent",
        timestamp=int(time.time()),
    )
    store.add_verdict(invoice_id, verdict, tx_hash)
    store.add_transaction(_make_tx(tx_hash, "post_verdict", invoice_id, verdict_data["x402_cost_motes"]))

    inv = store.get_invoice(invoice_id)
    return {
        "ok": True,
        "invoice": _invoice_dict(inv),
        "tx_hash": tx_hash,
        "tx_url": casper.tx_url(tx_hash) if tx_hash != "demo-tx" else "",
        "verdict": verdict_data,
        "message": f"AI verdict posted: {verdict_data['risk_band']} risk (score {verdict_data['score']}/1000)",
    }


@app.post("/api/invoice/{invoice_id}/fund")
async def fund_invoice(invoice_id: int, req: FundInvoiceReq = None):
    """LP funds an evaluated invoice."""
    inv = store.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status < InvoiceStatus.EVALUATED:
        raise HTTPException(400, "Invoice must be evaluated first")
    if inv.status >= InvoiceStatus.FUNDED:
        raise HTTPException(400, "Invoice already funded")

    # Calculate advance amount
    if req and req.advance_ratio:
        ratio = req.advance_ratio
    elif inv.verdict:
        ratio = inv.verdict.max_advance_bps / 10000
    else:
        ratio = 0.6

    advance_motes = str(int(int(inv.face_amount_motes) * ratio))

    tx_hash = "demo-tx"
    if CLI_AVAILABLE:
        try:
            result = casper.fund_invoice(invoice_id, advance_motes)
            tx_hash = result.get("tx_hash", "error")
        except Exception as e:
            raise HTTPException(500, f"On-chain funding failed: {str(e)[:200]}")

    store.fund_invoice(invoice_id, advance_motes, "hash-deployer", tx_hash)
    store.add_transaction(_make_tx(tx_hash, "fund_invoice", invoice_id, advance_motes))

    inv = store.get_invoice(invoice_id)
    return {
        "ok": True,
        "invoice": _invoice_dict(inv),
        "tx_hash": tx_hash,
        "tx_url": casper.tx_url(tx_hash) if tx_hash != "demo-tx" else "",
        "message": f"Invoice #{invoice_id} funded with {_cspr_from_motes(advance_motes):.2f} CSPR",
    }


@app.post("/api/invoice/{invoice_id}/settle")
async def settle_invoice(invoice_id: int):
    """Farmer repays the face value and settles the invoice."""
    inv = store.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status != InvoiceStatus.FUNDED:
        raise HTTPException(400, "Invoice must be FUNDED to settle")

    tx_hash = "demo-tx"
    if CLI_AVAILABLE:
        try:
            result = casper.repay_and_settle(invoice_id, inv.face_amount_motes)
            tx_hash = result.get("tx_hash", "error")
        except Exception as e:
            raise HTTPException(500, f"On-chain settle failed: {str(e)[:200]}")

    store.settle_invoice(invoice_id, tx_hash)
    store.add_transaction(_make_tx(tx_hash, "repay_and_settle", invoice_id, inv.face_amount_motes))

    inv = store.get_invoice(invoice_id)
    return {
        "ok": True,
        "invoice": _invoice_dict(inv),
        "tx_hash": tx_hash,
        "tx_url": casper.tx_url(tx_hash) if tx_hash != "demo-tx" else "",
        "message": f"Invoice #{invoice_id} settled. LP repaid + protocol fee distributed.",
    }


@app.get("/api/transactions")
async def get_transactions(limit: int = Query(20, ge=1, le=100)):
    return {"transactions": store.get_transactions(limit)}


@app.get("/api/chain/status")
async def chain_status():
    """Live Casper testnet status."""
    try:
        height = casper.get_chain_height()
        return {
            "network": casper.CHAIN_NAME,
            "block_height": height,
            "contract": casper.CONTRACT_HASH[:24] + "...",
            "rpc": casper.RPC_URL,
            "connected": True,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)[:200]}


# ── Static file serving (frontend pages) ────────────────────────────────────

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index_page():
    path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "index.html not found"}, status_code=404)


@app.get("/favicon.svg")
async def favicon():
    path = os.path.join(WEB_DIR, "favicon.svg")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/svg+xml")
    raise HTTPException(404, "favicon not found")


@app.get("/dashboard")
async def dashboard_page():
    path = os.path.join(WEB_DIR, "dashboard.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "dashboard.html not found")


@app.get("/farmer")
async def farmer_page():
    path = os.path.join(WEB_DIR, "farmer.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "farmer.html not found")


@app.get("/lp")
async def lp_page():
    path = os.path.join(WEB_DIR, "lp.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "lp.html not found")


def _make_tx(tx_hash: str, ep: str, inv_id: int | None, amount: str):
    from .state import Transaction
    return Transaction(
        tx_hash=tx_hash,
        entry_point=ep,
        invoice_id=inv_id,
        amount_motes=amount,
        timestamp=int(time.time()),
        status="executed" if tx_hash not in ("demo-tx", "error") else "pending",
        cspr_live_url=casper.tx_url(tx_hash) if tx_hash not in ("demo-tx", "error") else "",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
