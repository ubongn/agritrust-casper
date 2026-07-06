"""Typed data models for the underwriting workflow."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CropType(str, Enum):
    MAIZE = "maize"
    RICE = "rice"
    CASSAVA = "cassava"
    COFFEE = "coffee"
    COCOA = "cocoa"
    VEGETABLES = "vegetables"
    OTHER = "other"


class FarmerProfile(BaseModel):
    """The off-chain KYC/agronomic profile attached to every invoice."""

    farmer_id: str = Field(..., description="Stable farmer identifier (e.g. national ID hash)")
    name: str
    region: str = Field(..., description="Administrative region, e.g. 'Ashanti, Ghana'")
    latitude: float
    longitude: float
    plot_hectares: float
    crop: CropType
    # Credit history signals (0-100 unless noted)
    prior_loans: int = 0
    prior_repayments: int = 0
    cooperative_member: bool = False
    has_insurance: bool = False
    # On-chain identity
    casper_account_hash: Optional[str] = Field(
        None, description="Casper account-hash (66 hex, 00-prefixed) receiving loan funds"
    )


class Invoice(BaseModel):
    """An RWA invoice requesting financing."""

    invoice_id: str
    farmer: FarmerProfile
    crop: CropType
    quantity_kg: float
    # Historical farm-gate price per kg in USD (informed by x402 price feed)
    farm_gate_price_usd: float
    # Requested advance as fraction of invoice face value, e.g. 0.6 = 60%
    advance_ratio: float = 0.6
    # Expected harvest / settlement date as unix seconds
    settlement_due: int

    @property
    def face_value_usd(self) -> float:
        return round(self.quantity_kg * self.farm_gate_price_usd, 2)

    @property
    def advance_usd(self) -> float:
        return round(self.face_value_usd * self.advance_ratio, 2)


class DataSignal(BaseModel):
    """One off-chain signal gathered from an x402 data feed."""

    feed: str
    payload: dict
    cost_motes: int = 0


class Verdict(BaseModel):
    """The agent's financing decision, emitted on-chain."""

    approve: bool
    credit_score: int = Field(..., ge=0, le=100)
    advance_usd: float
    interest_bps: int = Field(..., ge=0, description="Annualized interest in basis points")
    rationale: str
    signals: list[DataSignal] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
