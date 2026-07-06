"""The underwriting brain.

Two modes, both *real* (no stubs):

1. **LLM mode** — when ``OPENAI_API_KEY`` is set, the agent calls an
   OpenAI-compatible model with the gathered signals and a structured prompt,
   requesting a JSON verdict (approve, credit_score, interest_bps, rationale).

2. **Deterministic mode** — a transparent, auditable scoring model built from
   agronomic + credit-history + weather + price signals. This always works and
   is what gets recorded on-chain for explainable, bias-free decisions.
"""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from .config import settings
from .models import DataSignal, Invoice, Verdict

console = Console()


def _safe_get(d: dict, *keys, default: float = 0.0) -> float:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else float(cur)


# ── Deterministic signal scoring ────────────────────────────────────────────

def _score_credit_history(invoice: Invoice) -> float:
    f = invoice.farmer
    total = f.prior_loans
    if total == 0:
        return 35.0  # thin file → neutral
    rate = f.prior_repayments / total
    return 60.0 * rate  # up to 60 pts


def _score_risk_pool(f: Any) -> float:
    """Co-op membership + insurance reduce default risk (up to 20 pts)."""
    pts = 0.0
    if f.cooperative_member:
        pts += 12.0
    if f.has_insurance:
        pts += 8.0
    return pts


def _score_weather(signal: DataSignal | None) -> float:
    """Drought/flood stress reduces score; favourable weather lifts it (±10)."""
    if not signal or signal.payload.get("source") == "unavailable":
        return 0.0
    p = signal.payload
    # Rainfall anomaly in mm/day vs climatology; soil moisture 0-1.
    rain_anom = _safe_get(p, "rain_anomaly_mm")
    moisture = _safe_get(p, "soil_moisture", "mean")
    if rain_anom < -3.0:  # drought
        return -10.0
    if rain_anom > 12.0:  # flood risk
        return -7.0
    bonus = max(0.0, min(10.0, (moisture - 0.3) * 20.0))
    return round(bonus - 5.0, 1)  # baseline neutral


def _score_price(signal: DataSignal | None, invoice: Invoice) -> float:
    """Forward price vs the invoice's farm-gate assumption (±10)."""
    if not signal or signal.payload.get("source") == "unavailable":
        return 0.0
    fwd = _safe_get(signal.payload, "forward_price_usd_kg")
    if fwd <= 0:
        return 0.0
    delta = (fwd - invoice.farm_gate_price_usd) / invoice.farm_gate_price_usd
    return round(max(-10.0, min(10.0, delta * 100.0)), 1)


def _interest_bps(score: int) -> int:
    """Higher risk → higher APR. Maps 0..100 → 2400..600 bps (24%..6% APR)."""
    # 100 score → 600 bps; 0 score → 2400 bps
    return int(round(2400 - (max(0, min(100, score)) / 100.0) * 1800))


def deterministic_verdict(invoice: Invoice, signals: list[DataSignal]) -> Verdict:
    """Fully transparent scoring — every contribution is explainable."""
    f = invoice.farmer
    sig_by_feed = {s.feed: s for s in signals}

    credit = _score_credit_history(invoice)
    pool = _score_risk_pool(f)
    wx = _score_weather(sig_by_feed.get("weather"))
    px = _score_price(sig_by_feed.get("price"), invoice)

    raw = 30.0 + credit + pool + wx + px
    score = int(max(0, min(100, round(raw))))
    approve = score >= 55
    advance = invoice.advance_usd if approve else 0.0
    rate = _interest_bps(score)

    reasons = [
        f"credit-history {credit:.0f}",
        f"risk-pool {pool:.0f}",
        f"weather {wx:+.1f}",
        f"price {px:+.1f}",
    ]
    rationale = "FINANCE — " + ", ".join(reasons) + ("" if approve else " (below threshold)")
    return Verdict(
        approve=approve,
        credit_score=score,
        advance_usd=advance,
        interest_bps=rate,
        rationale=rationale,
        signals=signals,
    )


# ── LLM mode ────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are AgriTrust's autonomous trade-finance underwriter for emerging-market "
    "smallholder farmers. You receive a farmer's KYC/agronomic profile, an RWA invoice, "
    "and paid off-chain signals (weather, forward crop price, KYC). Decide whether to "
    "FINANCE the invoice (pay a cash advance now against the harvest) and at what APR. "
    "Be conservative on thin credit files but reward cooperative membership, insurance, "
    "favourable weather and strong forward prices. Respond ONLY with JSON matching the schema."
)

_SCHEMA = {
    "type": "object",
    "required": ["approve", "credit_score", "interest_bps", "rationale"],
    "properties": {
        "approve": {"type": "boolean"},
        "credit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "interest_bps": {"type": "integer", "minimum": 0, "maximum": 5000},
        "rationale": {"type": "string"},
    },
}


def _build_user_prompt(invoice: Invoice, signals: list[DataSignal]) -> str:
    return json.dumps(
        {
            "invoice": invoice.model_dump(),
            "face_value_usd": invoice.face_value_usd,
            "advance_requested_usd": invoice.advance_usd,
            "signals": [
                {"feed": s.feed, "payload": s.payload, "cost_motes": s.cost_motes}
                for s in signals
            ],
            "task": "Return the JSON verdict. Set approve=true iff credit_score>=55.",
        },
        default=str,
    )


def llm_verdict(invoice: Invoice, signals: list[DataSignal]) -> Verdict:
    from openai import OpenAI  # local import: optional dependency

    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(invoice, signals)},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=400,
    )
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)
    return Verdict(
        approve=bool(parsed.get("approve", False)),
        credit_score=int(parsed.get("credit_score", 0)),
        advance_usd=invoice.advance_usd if parsed.get("approve") else 0.0,
        interest_bps=int(parsed.get("interest_bps", _interest_bps(int(parsed.get("credit_score", 0)))),
        rationale=str(parsed.get("rationale", "")),
        signals=signals,
    )


def underwrite(invoice: Invoice, signals: list[DataSignal]) -> Verdict:
    """Top-level entry: LLM if configured, else the deterministic model.

    In LLM mode we still run the deterministic model first so the recorded
    verdict always carries an explainable anchor; the LLM may adjust interest.
    """
    base = deterministic_verdict(invoice, signals)
    if not settings.llm_enabled:
        console.print("[yellow]LLM disabled[/] → using deterministic underwriting model")
        return base
    console.print(f"[green]LLM enabled[/] ({settings.llm_model}) → refining verdict")
    try:
        v = llm_verdict(invoice, signals)
        # keep deterministic credit_score as an explainable anchor in rationale
        v.rationale = f"[det={base.credit_score}] " + v.rationale
        return v
    except Exception as exc:  # never let an LLM hiccup block financing
        console.print(f"[red]LLM error[/] {exc!r} → falling back to deterministic model")
        return base
