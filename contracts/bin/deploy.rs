//! AgriTrust — Casper Testnet Deployment Binary
//!
//! Deploys the AgriTrust RWA trade-finance contract to the Casper Testnet
//! using Odra's livenet environment (CasperClient with V1 transactions).
//!
//! Prerequisites:
//!   1. `cargo odra build`  (produces wasm/AgriTrust.wasm)
//!   2. `.env` file at project root with testnet config
//!   3. Funded secret key (faucet at https://testnet.cspr.tools/)
//!
//! Run from the contracts/ directory (where Odra.toml, wasm/, .env live):
//!   set ODRA_LOG_LEVEL=debug
//!   cargo run --bin agritrust_deploy

#[cfg(not(target_arch = "wasm32"))]
use odra::host::Deployer;

/// Gas budget, derived from the Casper testnet chainspec (protocol 2.2.2):
///   block_gas_limit = 812_500_000_000   ← hard per-transaction cap
///   gas_per_byte    = 1_117_587         ← storage cost per byte in global state
/// The AgriTrust wasm is 338,750 bytes, so storing the module bytes alone costs
/// 338,750 × 1,117,587 ≈ 378.6B motes, plus ~10–50B of init execution (put_key,
/// entry-point/package storage, opcode costs) → true install cost ≈ 390–450B.
/// A first attempt at 200B ran out of gas; 1T was rejected as exceeding the
/// block_gas_limit. 700B sits ~75% above the true cost and safely below the cap.
/// On a successful deploy only gas actually used is charged (75% of the unused
/// remainder is refunded per chainspec refund_ratio = [75, 100]).
const DEPLOY_GAS: u64 = 700_000_000_000; // 700B — true cost ~400B, cap 812.5B

#[cfg(not(target_arch = "wasm32"))]
fn main() {
    use agritrust_contract::AgriTrust;
    use odra::prelude::Addressable;
    use odra_casper_livenet_env::env as livenet_env;

    println!("═══════════════════════════════════════════════");
    println!("  AgriTrust → Casper Testnet Deployment");
    println!("═══════════════════════════════════════════════\n");

    let env = livenet_env();
    env.set_gas(DEPLOY_GAS);

    let deployer = env.get_account(0);
    println!("Deployer address: {:?}\n", deployer);

    // ── Deploy AgriTrust ─────────────────────────────────────────────────
    // Init args:
    //   protocol_fee_bps: 50    = 0.5% fee taken from funder settlement
    //   grace_period_secs: 259200 = 3 days late-payment grace
    println!("[1/1] Deploying AgriTrust…");
    let contract = match AgriTrust::try_deploy(
        &env,
        agritrust_contract::AgriTrustInitArgs {
            protocol_fee_bps: 50,
            grace_period_secs: 259_200,
        },
    ) {
        Ok(c) => c,
        Err(e) => {
            // NOTE: the *underlying* Casper error is printed by Odra's log at
            // ERROR level before reaching here (it gets stringified to a
            // generic "Livenet execution error" wrapper). Re-run with
            // ODRA_LOG_LEVEL=debug to see the full transaction + real error.
            eprintln!("\n  ❌ Deploy failed: {:?}", e);
            eprintln!("  (See the '[ERROR] Transaction ... failed with error:' line above for the real Casper cause.)");
            std::process::exit(1);
        }
    };
    println!("  ✅ AgriTrust  → {:?}", contract.address());

    // ── Summary ──────────────────────────────────────────────────────────
    println!("\n═══════════════════════════════════════════════");
    println!("  🎉 AgriTrust deployed successfully!");
    println!("═══════════════════════════════════════════════");
    println!("  Contract       : {:?}", contract.address());
    println!("  Protocol fee   : 0.5% (50 bps)");
    println!("  Grace period   : 3 days (259200 s)");
    println!("═══════════════════════════════════════════════");
    println!("\nPost-deploy: Authorize the underwriting agent:");
    println!("  contract.authorize_agent(<agent_account_hash>);");
    println!("\nExplorer: https://testnet.cspr.live/account/{:?}\n", deployer);
}

#[cfg(target_arch = "wasm32")]
fn main() {}
