//! AgriTrust — Autonomous On-Chain Lifecycle Demo (LIVE Testnet)
//!
//! Attaches to the already-deployed AgriTrust contract and drives a full RWA
//! trade-finance lifecycle with **real Casper Testnet transactions**:
//!
//!   ① authorize_agent   — owner (deployer) onboards the AI agent
//!   ② register_invoice  — farmer tokenizes a harvest as an on-chain RWA
//!   ③ post_verdict      — AI agent posts a signed risk verdict (after x402 data purchase)
//!   ④ fund_invoice      — LP advances working capital to the farmer (native CSPR transfer)
//!   ⑤ repay_and_settle  — farmer repays at maturity; LP + treasury are settled
//!
//! The deployer plays every role (owner / agent / farmer / LP) for a clean
//! single-key demo. Every state transition is a real, gas-bearing transaction
//! on `casper-test`.
//!
//! Inputs are read from environment variables (set by the Python orchestrator
//! after underwriting + x402 payment); every value has a sensible default so
//! the binary also runs standalone:
//!
//!   cargo run --bin agritrust_demo
//!
//! Run from the `contracts/` directory (where `.env`, `Odra.toml` live).

#[cfg(not(target_arch = "wasm32"))]
use odra::host::{HostRef, HostRefLoader};

/// Gas budget per lifecycle transaction. These are small session calls (a few
/// storage reads/writes, at most two native transfers). 10B motes gives ample
/// headroom; Casper charges only gas actually consumed and refunds the rest.
const CALL_GAS: u64 = 10_000_000_000; // 10B motes (10 CSPR budget, actual cost much less)

#[cfg(not(target_arch = "wasm32"))]
fn main() {
    use agritrust_contract::AgriTrust;
    use odra::casper_types::U512;
    use odra::prelude::Address;
    use odra_casper_livenet_env::env as livenet_env;

    banner("AgriTrust → LIVE On-Chain Lifecycle Demo");

    let env = livenet_env();
    let deployer = env.get_account(0);

    println!("\n ┌─ Network ─────────────────────────────────────────");
    println!(" │  chain      : casper-test (protocol 2.2.2)");
    println!(" │  agent/owner: {}", short_addr(&deployer));
    println!(" └──────────────────────────────────────────────────\n");

    // ── Resolve the deployed contract package hash ────────────────────────
    let hash_str = env_str(
        "AGRITRUST_CONTRACT_HASH",
        "hash-c1dfe36ea24cac44224608ad69c880aedd0101cca405fbd686e461ac3d1bd29b",
    );
    let address: Address = hash_str
        .parse()
        .unwrap_or_else(|e| panic!("invalid contract hash '{}': {:?}", hash_str, e));

    println!("Attaching to AgriTrust @ {}", hash_str);
    let mut contract = AgriTrust::load(&env, address);
    println!(" ✓ Attached to live contract.\n");

    // ── Read demo inputs (from orchestrator env vars, with defaults) ──────
    let commodity = env_str("AGRITRUST_COMMODITY", "Maize");
    let region = env_str("AGRITRUST_REGION", "Ashanti, Ghana");
    let face_amount = u512_env("AGRITRUST_FACE_MOTES", "100000000000"); // 100 CSPR
    let maturity_offset = u64_env("AGRITRUST_MATURITY_OFFSET", 30);
    let score = u32_env("AGRITRUST_SCORE", 820);
    let risk_band = env_str("AGRITRUST_RISK_BAND", "AA");
    let max_advance_bps = u32_env("AGRITRUST_MAX_ADVANCE_BPS", 8000); // 80%
    let discount_rate_bps = u32_env("AGRITRUST_DISCOUNT_BPS", 750); // 7.5%
    let data_hash = env_str("AGRITRUST_DATA_HASH", "demo-blake2b-placeholder");
    let x402_cost = u512_env("AGRITRUST_X402_COST_MOTES", "100000000"); // 0.1 CSPR

    println!(" ┌─ RWA being financed ─────────────────────────────");
    println!(" │  commodity   : {}", commodity);
    println!(" │  region      : {}", region);
    println!(" │  face value  : {} motes ({} CSPR)", face_amount, motes_to_cspr(&face_amount));
    println!(" └──────────────────────────────────────────────────\n");

    // ══════════════════════════════════════════════════════════════════════
    //  ① AUTHORIZE AGENT  — owner onboards the deployer as an AI agent
    // ══════════════════════════════════════════════════════════════════════
    stage(1, "Authorize AI Agent");
    env.set_gas(CALL_GAS);
    contract.authorize_agent(deployer);
    println!(" ✓ Agent authorized (is_authorized={}).\n", contract.is_authorized_agent(deployer));

    // ══════════════════════════════════════════════════════════════════════
    //  ② REGISTER INVOICE  — tokenize the harvest as an on-chain RWA
    // ══════════════════════════════════════════════════════════════════════
    stage(2, "Tokenize Invoice (Register RWA)");
    let now = env.block_time(); // milliseconds — matches the contract's get_block_time()
    let maturity = now + (maturity_offset * 1000);
    env.set_gas(CALL_GAS);
    let invoice_id = contract.register_invoice(
        commodity.clone(),
        region.clone(),
        face_amount,
        maturity,
    );
    println!(" ✓ RWA tokenized → invoice_id = {}", invoice_id);
    println!("   maturity = {} (T+{}s)\n", maturity, maturity_offset);

    // ══════════════════════════════════════════════════════════════════════
    //  ③ POST VERDICT  — AI agent posts the underwriting verdict
    // ══════════════════════════════════════════════════════════════════════
    stage(3, "Post AI Underwriting Verdict");
    println!("   score={}, band={}, advance≤{}bps, discount={}bps",
             score, risk_band, max_advance_bps, discount_rate_bps);
    println!("   data_hash={}, x402_cost={} motes", data_hash, x402_cost);
    env.set_gas(CALL_GAS);
    contract.post_verdict(
        invoice_id,
        score,
        risk_band.clone(),
        max_advance_bps,
        discount_rate_bps,
        data_hash.clone(),
        x402_cost,
    );
    println!(" ✓ Verdict posted on-chain (invoice now EVALUATED).\n");

    // ══════════════════════════════════════════════════════════════════════
    //  ④ FUND INVOICE  — LP advances capital to the farmer
    // ══════════════════════════════════════════════════════════════════════
    stage(4, "Fund Invoice (LP Advances Capital)");
    let advance_amount = face_amount * U512::from(max_advance_bps) / U512::from(10_000u32);
    println!("   advancing {} motes ({} CSPR) to farmer…", advance_amount, motes_to_cspr(&advance_amount));
    env.set_gas(CALL_GAS);
    contract.fund_invoice(invoice_id, advance_amount);
    println!(" ✓ Funded — capital transferred to farmer (invoice now FUNDED).\n");

    // ══════════════════════════════════════════════════════════════════════
    //  ⑤ REPAY & SETTLE  — wait for maturity, then the farmer repays
    // ══════════════════════════════════════════════════════════════════════
    stage(5, "Repay & Settle at Maturity");
    wait_for_maturity(&env, maturity);

    println!("   farmer repays full face value {} motes…", face_amount);
    env.set_gas(CALL_GAS);
    contract.repay_and_settle(invoice_id);
    println!(" ✓ Settled — LP paid out, protocol fee collected (invoice SETTLED).\n");

    // ══════════════════════════════════════════════════════════════════════
    //  Read back final on-chain state
    // ══════════════════════════════════════════════════════════════════════
    let inv = contract.get_invoice(invoice_id);
    let stats = contract.stats();
    let fee_bps = contract.protocol_fee_bps();

    banner("On-Chain State — Verified");
    println!("\n ┌─ Invoice #{} ──────────────────────────────────", invoice_id);
    println!(" │  commodity    : {}", inv.commodity);
    println!(" │  region       : {}", inv.region);
    println!(" │  face value   : {} motes", inv.face_amount);
    println!(" │  advance      : {} motes", inv.advance_amount);
    println!(" │  maturity     : {}", inv.maturity);
    println!(" │  status       : {} ({})", inv.status, status_name(inv.status));
    println!(" └──────────────────────────────────────────────────");
    println!("\n ┌─ Protocol Totals ────────────────────────────────");
    println!(" │  total invoices  : {}", stats.total_invoices);
    println!(" │  total funded    : {} motes ({} CSPR)", stats.total_funded, motes_to_cspr(&stats.total_funded));
    println!(" │  total settled   : {} motes ({} CSPR)", stats.total_settled, motes_to_cspr(&stats.total_settled));
    println!(" │  total defaulted : {} motes", stats.total_defaulted);
    println!(" │  total x402 spent: {} motes ({} CSPR)", stats.total_x402_spent, motes_to_cspr(&stats.total_x402_spent));
    println!(" │  active agents   : {}", stats.active_agents);
    println!(" │  protocol fee    : {} bps ({}%)", fee_bps, fee_bps as f64 / 100.0);
    println!(" └──────────────────────────────────────────────────");

    banner("✅ AgriTrust lifecycle complete — all stages executed on-chain");
}

// ──────────────────────────────────────────────────────────────────────────────
//  Helpers
// ──────────────────────────────────────────────────────────────────────────────

fn banner(title: &str) {
    let line = "═".repeat(title.len().min(60) + 4);
    println!("\n{}\n  {}\n{}", line, title, line);
}

fn stage(n: u8, title: &str) {
    println!("\n ┌─ {} {} ", num(n), title);
    println!(" └────────────────────────────────────────────────────");
}

fn num(n: u8) -> char {
    ['①','②','③','④','⑤','⑥','⑦','⑧','⑨'][(n as usize).saturating_sub(1)]
}

fn status_name(s: u8) -> &'static str {
    match s {
        0 => "REGISTERED",
        1 => "EVALUATED",
        2 => "FUNDED",
        3 => "SETTLED",
        4 => "DEFAULTED",
        _ => "UNKNOWN",
    }
}

fn motes_to_cspr(m: &odra::casper_types::U512) -> String {
    let whole = m / odra::casper_types::U512::from(1_000_000_000u64);
    let frac = m % odra::casper_types::U512::from(1_000_000_000u64);
    format!("{}.{:09}", whole, frac)
}

fn short_addr(a: &odra::prelude::Address) -> String {
    format!("{:?}", a)
}

/// Poll the live block time until maturity is reached, printing progress.
#[cfg(not(target_arch = "wasm32"))]
fn wait_for_maturity(env: &odra::host::HostEnv, maturity: u64) {
    loop {
        let now = env.block_time();
        if now >= maturity {
            println!(" ✓ Maturity reached (block_time={} ≥ maturity={}).", now, maturity);
            return;
        }
        // block_time() returns milliseconds on livenet.
        let remaining_s = (maturity - now) / 1000;
        let sleep_s = remaining_s.min(20).max(1);
        println!("   ⏳ waiting for maturity: {}s remaining (block_time={})…", remaining_s, now);
        std::thread::sleep(std::time::Duration::from_secs(sleep_s));
    }
}

// ── Env-var readers with defaults ─────────────────────────────────────────────

fn env_str(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn u64_env(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

fn u32_env(key: &str, default: u32) -> u32 {
    std::env::var(key)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

#[cfg(not(target_arch = "wasm32"))]
fn u512_env(key: &str, default: &str) -> odra::casper_types::U512 {
    let raw = env_str(key, default);
    odra::casper_types::U512::from_dec_str(&raw).unwrap_or_else(|_| {
        odra::casper_types::U512::from_dec_str(default)
            .expect("default must be a valid decimal")
    })
}

/// Decode a 64-char hex string (optionally prefixed with `hash-`) into 32 bytes.
#[allow(dead_code)]
fn decode_hex_32(input: &str) -> [u8; 32] {
    let s = input.trim_start_matches("hash-").trim_start_matches("0x");
    assert!(
        s.len() == 64,
        "expected a 64-char (32-byte) hex hash, got {} chars: {}",
        s.len(),
        s
    );
    let mut out = [0u8; 32];
    for i in 0..32 {
        out[i] = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16)
            .unwrap_or_else(|e| panic!("invalid hex byte at {}: {}", i, e));
    }
    out
}
