//! AgriTrust Custom WASM Proxy Caller — Casper 2.2.2 Compatible
//!
//! Replaces Odra's broken proxy_caller_with_return.wasm which was compiled
//! against pre-Condor Casper FFI and produces garbage purse balances on
//! Casper 2.2.2 (protocol 2.0+).
//!
//! Compiled to wasm32-unknown-unknown and deployed as inline session code:
//!   1. Creates a cargo purse
//!   2. Funds it from the caller's main purse
//!   3. Calls the target contract entry point with cargo_purse attached

#![no_std]
#![no_main]

extern crate alloc;

use alloc::string::String;
use casper_contract::contract_api::{account, runtime, system};
use casper_contract::unwrap_or_revert::UnwrapOrRevert;
use casper_types::{
    bytesrepr::{Bytes, FromBytes}, contracts::ContractPackageHash, ApiError, RuntimeArgs, U512,
};

#[global_allocator]
static ALLOC: wee_alloc::WeeAlloc = wee_alloc::WeeAlloc::INIT;

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    runtime::revert(ApiError::User(99));
}

// ── Arg names (must match what the Rust demo binary passes) ─────────────────
const CONTRACT_HASH_ARG: &str = "contract_hash";
const ENTRY_POINT_ARG: &str = "entry_point";
const AMOUNT_ARG: &str = "amount";
const ARGS_ARG: &str = "args";
const CARGO_PURSE_ARG: &str = "cargo_purse";

#[no_mangle]
pub extern "C" fn call() {
    let contract_hash: ContractPackageHash = runtime::get_named_arg(CONTRACT_HASH_ARG);
    let entry_point: String = runtime::get_named_arg(ENTRY_POINT_ARG);
    let amount: U512 = runtime::get_named_arg(AMOUNT_ARG);
    let args_bytes: Bytes = runtime::get_named_arg(ARGS_ARG);

    if amount.is_zero() {
        let (inner_args, _) = RuntimeArgs::from_bytes(&args_bytes).unwrap_or_revert();
        let _: () =
            runtime::call_versioned_contract(contract_hash, None, &entry_point, inner_args);
        return;
    }

    // ── Create and fund a cargo purse ──────────────────────────────────────
    let main_purse = account::get_main_purse();
    let cargo_purse = system::create_purse();
    system::transfer_from_purse_to_purse(main_purse, cargo_purse, amount, None)
        .unwrap_or_revert();

    // ── Deserialize inner args and inject cargo_purse ─────────────────────
    let (mut inner_args, _) = RuntimeArgs::from_bytes(&args_bytes).unwrap_or_revert();
    inner_args
        .insert(CARGO_PURSE_ARG, cargo_purse)
        .unwrap_or_revert();

    // ── Call the target contract entry point ──────────────────────────────
    let _: () = runtime::call_versioned_contract(contract_hash, None, &entry_point, inner_args);
}
