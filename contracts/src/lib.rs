//! # AgriTrust — Autonomous Trade-Finance Protocol for Emerging-Market Farmers
//!
//! A self-driving credit + settlement protocol built with the [Odra](https://odra.dev)
//! framework for the Casper Network. An SME farmer tokenizes a harvest/invoice as a
//! Real-World Asset (RWA). An AI underwriting agent evaluates creditworthiness from
//! off-chain data (paid for via x402 micropayments) and posts a signed risk verdict
//! on-chain. Liquidity providers fund the invoice at a discount, giving the farmer
//! instant working capital. The agent runs the full lifecycle:
//! **evaluate → tokenize → fund → collect → settle**.

#![cfg_attr(not(test), no_std)]
#![allow(unexpected_cfgs)]

use odra::casper_types::account::AccountHash;
use odra::casper_types::U512;
use odra::prelude::*;

// ──────────────────────────────────────────────────────────────────────────────
//  Constants
// ──────────────────────────────────────────────────────────────────────────────

/// Basis points in one whole (100%).
const BPS_DENOM: u32 = 10_000;
/// Status codes for an invoice's lifecycle.
const STATUS_REGISTERED: u8 = 0;
const STATUS_EVALUATED: u8 = 1;
const STATUS_FUNDED: u8 = 2;
const STATUS_SETTLED: u8 = 3;
const STATUS_DEFAULTED: u8 = 4;

// ──────────────────────────────────────────────────────────────────────────────
//  Errors
// ──────────────────────────────────────────────────────────────────────────────

#[odra::odra_error]
pub enum Error {
    NotOwner = 1,
    NotAuthorized = 2,
    InvoiceNotFound = 3,
    NotEvaluated = 5,
    InsufficientPayment = 6,
    CannotRepayEarly = 14,
    NotFunded = 12,
    WrongStatus = 13,
    AdvanceTooHigh = 9,
    InvalidAmount = 10,
    UnauthorizedFunder = 11,
    NotPastGrace = 15,
    VerdictNotFound = 16,
}

/// Sentinel address used before an invoice is funded (all-zero account hash).
fn zero_address() -> Address {
    Address::Account(AccountHash::new([0u8; 32]))
}

// ──────────────────────────────────────────────────────────────────────────────
//  Data types (RWA + underwriting model)
// ──────────────────────────────────────────────────────────────────────────────

/// A tokenized farmer invoice — the Real-World Asset (RWA) tracked on-chain.
#[odra::odra_type]
pub struct Invoice {
    /// Sequential RWA token id.
    pub id: u64,
    /// The farmer / SME who owns the harvest (on-chain account).
    pub farmer: Address,
    /// Commodity, e.g. "Cocoa", "Maize", "Coffee".
    pub commodity: String,
    /// Production region, e.g. "Ashanti, Ghana".
    pub region: String,
    /// Full face value of the invoice, in motes.
    pub face_amount: U512,
    /// Working capital advanced to the farmer by the funder (after discount), in motes.
    pub advance_amount: U512,
    /// Unix timestamp (seconds) at which the invoice is due for repayment.
    pub maturity: u64,
    /// Lifecycle status (see STATUS_* constants).
    pub status: u8,
    /// The liquidity provider that funded the invoice (zero address until funded).
    pub funder: Address,
    /// Block time when the RWA was registered.
    pub created_at: u64,
}

/// A signed underwriting verdict posted by the AI agent after x402-funded data checks.
#[odra::odra_type]
pub struct RiskVerdict {
    pub invoice_id: u64,
    /// Credit score in the 0–1000 range (higher = safer).
    pub score: u32,
    /// Human-readable risk band, e.g. "AAA", "BB", "C".
    pub risk_band: String,
    /// Maximum share of the face value (bps) an LP may advance, e.g. 8000 = 80%.
    pub max_advance_bps: u32,
    /// Recommended annualized discount rate (bps) the LP should charge.
    pub discount_rate_bps: u32,
    /// Cryptographic hash of the off-chain underwriting dataset (yield, weather, price).
    pub data_hash: String,
    /// The authorized agent that produced the verdict.
    pub agent: Address,
    /// Verdict timestamp.
    pub timestamp: u64,
    /// Total x402 micropayments (motes) the agent spent on data feeds for this verdict.
    pub x402_cost: U512,
}

/// Aggregated protocol statistics (for dashboards / agents).
#[odra::odra_type]
pub struct ProtocolStats {
    pub total_invoices: u64,
    pub total_funded: U512,
    pub total_settled: U512,
    pub total_defaulted: U512,
    pub total_x402_spent: U512,
    pub active_agents: u32,
}

// ──────────────────────────────────────────────────────────────────────────────
//  Events
// ──────────────────────────────────────────────────────────────────────────────

#[odra::event]
pub struct InvoiceRegistered {
    pub invoice_id: u64,
    pub farmer: Address,
    pub commodity: String,
    pub face_amount: U512,
}

#[odra::event]
pub struct VerdictPosted {
    pub invoice_id: u64,
    pub score: u32,
    pub risk_band: String,
    pub agent: Address,
    pub x402_cost: U512,
}

#[odra::event]
pub struct InvoiceFunded {
    pub invoice_id: u64,
    pub funder: Address,
    pub advance_amount: U512,
}

#[odra::event]
pub struct InvoiceSettled {
    pub invoice_id: u64,
    pub funder: Address,
    pub repayment: U512,
    pub fee: U512,
}

#[odra::event]
pub struct InvoiceDefaulted {
    pub invoice_id: u64,
    pub funder: Address,
}

#[odra::event]
pub struct AgentAuthorized {
    pub agent: Address,
}

// ──────────────────────────────────────────────────────────────────────────────
//  Contract module
// ──────────────────────────────────────────────────────────────────────────────

/// AgriTrust — the autonomous trade-finance engine.
#[odra::module]
pub struct AgriTrust {
    /// Protocol owner (deployer). Authorizes agents and receives protocol fees.
    owner: Var<Address>,
    /// Allow-listed AI underwriting agents that may post verdicts / mark defaults.
    authorized_agents: Var<Vec<Address>>,
    /// RWA token counter.
    invoice_count: Var<u64>,
    /// RWA invoice registry: id -> Invoice.
    invoices: Mapping<u64, Invoice>,
    /// Underwriting verdicts: invoice id -> RiskVerdict.
    verdicts: Mapping<u64, RiskVerdict>,
    /// Cumulative CSPR advanced to farmers (motes).
    total_funded: Var<U512>,
    /// Cumulative CSPR repaid by farmers (motes).
    total_settled: Var<U512>,
    /// Cumulative CSPR written off on default (motes).
    total_defaulted: Var<U512>,
    /// Cumulative x402 micropayments spent on data feeds (motes).
    total_x402_spent: Var<U512>,
    /// Protocol fee charged on settlement, in basis points (e.g. 50 = 0.5%).
    protocol_fee_bps: Var<u32>,
    /// Default-grace period in seconds after maturity before an LP may claim default.
    grace_period_secs: Var<u64>,
}

#[odra::module]
impl AgriTrust {
    // ── Initialization ───────────────────────────────────────────────────────

    /// Deploys the protocol. `protocol_fee_bps` is taken from the funder's settlement
    /// payout (e.g. 50 = 0.5%). The grace period lets farmers pay a little late before
    /// a funder can declare default.
    pub fn init(&mut self, protocol_fee_bps: u32, grace_period_secs: u64) {
        self.assert(self.protocol_fee_bps_valid(protocol_fee_bps), Error::InvalidAmount);
        self.assert(grace_period_secs < 90 * 86_400, Error::InvalidAmount);
        self.owner.set(self.env().caller());
        self.authorized_agents.set(Vec::new());
        self.invoice_count.set(0);
        self.total_funded.set(U512::zero());
        self.total_settled.set(U512::zero());
        self.total_defaulted.set(U512::zero());
        self.total_x402_spent.set(U512::zero());
        self.protocol_fee_bps.set(protocol_fee_bps);
        self.grace_period_secs.set(grace_period_secs);
    }

    // ── Access control ───────────────────────────────────────────────────────

    /// Owner-only: authorize an AI underwriting agent.
    pub fn authorize_agent(&mut self, agent: Address) {
        self.assert_owner();
        let mut agents = self.authorized_agents.get_or_default();
        if !agents.contains(&agent) {
            agents.push(agent);
            self.authorized_agents.set(agents);
            self.env().emit_event(AgentAuthorized { agent });
        }
    }

    /// Owner-only: revoke an agent.
    pub fn revoke_agent(&mut self, agent: Address) {
        self.assert_owner();
        let mut agents = self.authorized_agents.get_or_default();
        agents.retain(|a| *a != agent);
        self.authorized_agents.set(agents);
    }

    pub fn is_authorized_agent(&self, agent: Address) -> bool {
        self.authorized_agents.get_or_default().contains(&agent)
    }

    pub fn set_protocol_fee_bps(&mut self, fee_bps: u32) {
        self.assert_owner();
        self.assert(self.protocol_fee_bps_valid(fee_bps), Error::InvalidAmount);
        self.protocol_fee_bps.set(fee_bps);
    }

    // ── RWA tokenization ────────────────────────────────────────────────────

    /// A farmer tokenizes a harvest/invoice as an on-chain RWA. Returns the new RWA id.
    pub fn register_invoice(
        &mut self,
        commodity: String,
        region: String,
        face_amount: U512,
        maturity: u64,
    ) -> u64 {
        self.assert(!face_amount.is_zero(), Error::InvalidAmount);
        let now = self.env().get_block_time();
        self.assert(maturity > now, Error::InvalidAmount);

        let id = self.invoice_count.get_or_default();
        self.invoice_count.set(id + 1);

        let farmer = self.env().caller();
        let event_commodity = commodity.clone();
        let invoice = Invoice {
            id,
            farmer,
            commodity,
            region,
            face_amount,
            advance_amount: U512::zero(),
            maturity,
            status: STATUS_REGISTERED,
            funder: zero_address(),
            created_at: now,
        };
        self.invoices.set(&id, invoice);

        self.env().emit_event(InvoiceRegistered {
            invoice_id: id,
            farmer,
            commodity: event_commodity,
            face_amount,
        });

        id
    }

    // ── AI underwriting verdict ──────────────────────────────────────────────

    /// An authorized AI agent posts a signed risk verdict for an invoice, after paying
    /// for off-chain data feeds (weather, market price, KYC) via x402 micropayments.
    /// This advances the invoice to the EVALUATED state, making it fundable.
    pub fn post_verdict(
        &mut self,
        invoice_id: u64,
        score: u32,
        risk_band: String,
        max_advance_bps: u32,
        discount_rate_bps: u32,
        data_hash: String,
        x402_cost: U512,
    ) {
        self.assert_agent();
        self.assert(score <= 1000, Error::InvalidAmount);
        self.assert(max_advance_bps <= BPS_DENOM, Error::AdvanceTooHigh);
        self.assert(discount_rate_bps <= BPS_DENOM, Error::AdvanceTooHigh);

        let mut invoice = self.invoice_or_revert(invoice_id);
        self.assert(invoice.status == STATUS_REGISTERED, Error::WrongStatus);

        let band = risk_band.clone();
        let agent = self.env().caller();
        let verdict = RiskVerdict {
            invoice_id,
            score,
            risk_band,
            max_advance_bps,
            discount_rate_bps,
            data_hash,
            agent,
            timestamp: self.env().get_block_time(),
            x402_cost,
        };
        self.verdicts.set(&invoice_id, verdict);

        // Accumulate on-chain proof of the agent's x402 data-feed spend.
        let mut spent = self.total_x402_spent.get_or_default();
        spent += x402_cost;
        self.total_x402_spent.set(spent);

        invoice.status = STATUS_EVALUATED;
        self.invoices.set(&invoice_id, invoice);

        self.env().emit_event(VerdictPosted {
            invoice_id,
            score,
            risk_band: band,
            agent,
            x402_cost,
        });
    }

    // ── Funding (instant working capital) ───────────────────────────────────

    /// A liquidity provider funds an evaluated invoice. The exact advance (in motes)
    /// must be attached as native CSPR value with this call. The advance must not exceed
    /// the agent's `max_advance_bps` cap. The full advance is paid out to the farmer and
    /// the funder receives the RWA claim (repaid the face value at maturity).
    pub fn fund_invoice(&mut self, invoice_id: u64, advance_amount: U512) {
        self.assert(!advance_amount.is_zero(), Error::InvalidAmount);

        let mut invoice = self.invoice_or_revert(invoice_id);
        self.assert(invoice.status == STATUS_EVALUATED, Error::NotEvaluated);

        // Enforce the agent's risk cap: advance <= face * max_advance_bps.
        let verdict = self.verdict_or_revert(invoice_id);
        let max_advance =
            invoice.face_amount * U512::from(verdict.max_advance_bps) / U512::from(BPS_DENOM);
        self.assert(advance_amount <= max_advance, Error::AdvanceTooHigh);

        // In production the funder attaches exactly the advance as native value
        // and the contract calls transfer_tokens. On the Casper testnet demo
        // (Odra livenet env) native transfers inside contract calls aren't
        // supported, so we record the obligation on-chain instead.
        let _attached = self.env().attached_value(); // ignored in livenet demo

        let farmer = invoice.farmer;
        let funder = self.env().caller();
        invoice.advance_amount = advance_amount;
        invoice.funder = funder;
        invoice.status = STATUS_FUNDED;
        self.invoices.set(&invoice_id, invoice);

        // Pay the farmer now — instant working capital.
        // (On mainnet: self.env().transfer_tokens(&farmer, &advance_amount);)

        let mut total = self.total_funded.get_or_default();
        total += advance_amount;
        self.total_funded.set(total);

        self.env().emit_event(InvoiceFunded {
            invoice_id,
            funder,
            advance_amount,
        });
    }

    // ── Repayment & settlement ──────────────────────────────────────────────

    /// The farmer repays the full face value at (or after) maturity. The funder is paid
    /// the face value minus the protocol fee; the fee goes to the protocol owner.
    pub fn repay_and_settle(&mut self, invoice_id: u64) {
        let mut invoice = self.invoice_or_revert(invoice_id);
        self.assert(invoice.status == STATUS_FUNDED, Error::NotFunded);

        let now = self.env().get_block_time();
        self.assert(now >= invoice.maturity, Error::CannotRepayEarly);

        let face = invoice.face_amount;
        // In production the farmer attaches the face value as native CSPR.
        // On the livenet demo env native transfers aren't supported.
        let _attached = self.env().attached_value(); // ignored in livenet demo

        let funder = invoice.funder;
        let fee_bps = self.protocol_fee_bps.get_or_default();
        let fee = face * U512::from(fee_bps) / U512::from(BPS_DENOM);
        let payout = face - fee;

        invoice.status = STATUS_SETTLED;
        self.invoices.set(&invoice_id, invoice);

        // Settle: funder receives the face value (minus fee); treasury receives the fee.
        // (On mainnet: self.env().transfer_tokens(&funder, &payout); etc.)
        let owner = self.owner_addr();
        let _ = (owner, fee, payout); // referenced for clarity

        let mut settled = self.total_settled.get_or_default();
        settled += face;
        self.total_settled.set(settled);

        self.env().emit_event(InvoiceSettled {
            invoice_id,
            funder,
            repayment: face,
            fee,
        });
    }

    /// If an invoice is past maturity + grace and still unpaid, the funder (or an agent)
    /// may declare default. The default is recorded for on-chain credit reputation.
    pub fn declare_default(&mut self, invoice_id: u64) {
        let caller = self.env().caller();
        let mut invoice = self.invoice_or_revert(invoice_id);
        self.assert(invoice.status == STATUS_FUNDED, Error::NotFunded);

        let now = self.env().get_block_time();
        let grace = self.grace_period_secs.get_or_default();
        self.assert(now >= invoice.maturity + grace, Error::NotPastGrace);

        let is_funder = caller == invoice.funder;
        let is_agent = self.is_authorized_agent(caller);
        self.assert(is_funder || is_agent, Error::UnauthorizedFunder);

        let advance = invoice.advance_amount;
        let funder = invoice.funder;
        invoice.status = STATUS_DEFAULTED;
        self.invoices.set(&invoice_id, invoice);

        let mut defaulted = self.total_defaulted.get_or_default();
        defaulted += advance;
        self.total_defaulted.set(defaulted);

        self.env().emit_event(InvoiceDefaulted {
            invoice_id,
            funder,
        });
    }

    // ── Read API ────────────────────────────────────────────────────────────

    pub fn get_invoice(&self, invoice_id: u64) -> Invoice {
        self.invoice_or_revert(invoice_id)
    }

    pub fn get_verdict(&self, invoice_id: u64) -> Option<RiskVerdict> {
        self.verdicts.get(&invoice_id)
    }

    pub fn total_invoices(&self) -> u64 {
        self.invoice_count.get_or_default()
    }

    pub fn total_funded(&self) -> U512 {
        self.total_funded.get_or_default()
    }

    pub fn total_settled(&self) -> U512 {
        self.total_settled.get_or_default()
    }

    pub fn total_defaulted(&self) -> U512 {
        self.total_defaulted.get_or_default()
    }

    pub fn total_x402_spent(&self) -> U512 {
        self.total_x402_spent.get_or_default()
    }

    pub fn owner(&self) -> Address {
        self.owner_addr()
    }

    pub fn protocol_fee_bps(&self) -> u32 {
        self.protocol_fee_bps.get_or_default()
    }

    pub fn grace_period_secs(&self) -> u64 {
        self.grace_period_secs.get_or_default()
    }

    pub fn stats(&self) -> ProtocolStats {
        ProtocolStats {
            total_invoices: self.invoice_count.get_or_default(),
            total_funded: self.total_funded.get_or_default(),
            total_settled: self.total_settled.get_or_default(),
            total_defaulted: self.total_defaulted.get_or_default(),
            total_x402_spent: self.total_x402_spent.get_or_default(),
            active_agents: self.authorized_agents.get_or_default().len() as u32,
        }
    }

    // ── Internal helpers (not entry points) ──────────────────────────────────

    fn assert(&self, condition: bool, error: Error) {
        if !condition {
            self.env().revert(error);
        }
    }

    fn assert_owner(&self) {
        self.assert(self.env().caller() == self.owner_addr(), Error::NotOwner);
    }

    fn assert_agent(&self) {
        self.assert(
            self.is_authorized_agent(self.env().caller()),
            Error::NotAuthorized,
        );
    }

    fn owner_addr(&self) -> Address {
        self.owner.get().unwrap_or_else(|| zero_address())
    }

    fn protocol_fee_bps_valid(&self, fee_bps: u32) -> bool {
        fee_bps < BPS_DENOM
    }

    fn invoice_or_revert(&self, invoice_id: u64) -> Invoice {
        match self.invoices.get(&invoice_id) {
            Some(i) => i,
            None => self.env().revert(Error::InvoiceNotFound),
        }
    }

    fn verdict_or_revert(&self, invoice_id: u64) -> RiskVerdict {
        match self.verdicts.get(&invoice_id) {
            Some(v) => v,
            None => self.env().revert(Error::VerdictNotFound),
        }
    }
}
