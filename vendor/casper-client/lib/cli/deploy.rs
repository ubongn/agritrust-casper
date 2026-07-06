//! Functions facilitating sending of [`Deploy`]s to the network

use casper_types::{
    account::AccountHash, ActivationPoint, AsymmetricType, CoreConfig, Deploy,
    ExecutableDeployItem, HashAddr, HighwayConfig, Key, ProtocolVersion, PublicKey, RuntimeArgs,
    StorageCosts, SystemConfig, TransactionConfig, TransferTarget, UIntParseError, URef,
    VacancyConfig, WasmConfig, U512,
};
use serde::{Deserialize, Serialize};

use super::{
    get_block, parse, query_global_state, transaction::get_maybe_secret_key, CliError,
    DeployStrParams, PaymentStrParams, SessionStrParams,
};
use crate::{
    cli::DeployBuilder,
    rpcs::results::{PutDeployResult, SpeculativeExecResult},
    SuccessResponse, MAX_SERIALIZED_SIZE_OF_DEPLOY,
};

const DEFAULT_GAS_PRICE: u64 = 1;

#[derive(PartialEq, Eq, Serialize, Deserialize, Debug)]
// Disallow unknown fields to ensure config files and command-line overrides contain valid keys.
#[serde(deny_unknown_fields)]
struct TomlNetwork {
    name: String,
    maximum_net_message_size: u32,
}

#[derive(PartialEq, Eq, Serialize, Deserialize, Debug)]
// Disallow unknown fields to ensure config files and command-line overrides contain valid keys.
#[serde(deny_unknown_fields)]
struct TomlProtocol {
    version: ProtocolVersion,
    hard_reset: bool,
    activation_point: ActivationPoint,
}

/// A chainspec configuration as laid out in the TOML-encoded configuration file.
#[derive(PartialEq, Eq, Serialize, Deserialize, Debug)]
// Disallow unknown fields to ensure config files and command-line overrides contain valid keys.
#[serde(deny_unknown_fields)]
pub(super) struct TomlChainspec {
    protocol: TomlProtocol,
    network: TomlNetwork,
    core: CoreConfig,
    transactions: TransactionConfig,
    highway: HighwayConfig,
    wasm: WasmConfig,
    system_costs: SystemConfig,
    vacancy: VacancyConfig,
    storage_costs: StorageCosts,
}

pub(crate) async fn do_withdraw_amount_checks(
    node_address: &str,
    verbosity_level: u64,
    public_key: PublicKey,
    amount: U512,
    min_bid_override: bool,
) -> Result<(), CliError> {
    let chainspec_bytes = crate::cli::get_chainspec("", node_address, verbosity_level)
        .await?
        .result
        .chainspec_bytes;

    let chainspec_as_str = std::str::from_utf8(chainspec_bytes.chainspec_bytes())
        .map_err(|_| CliError::FailedToParseChainspecBytes)?;
    let toml_chainspec: TomlChainspec =
        toml::from_str(chainspec_as_str).map_err(|_| CliError::FailedToParseChainspecBytes)?;

    let minimum_validator_bid = toml_chainspec.core.minimum_bid_amount;

    match crate::cli::get_auction_info("", node_address, verbosity_level, "")
        .await?
        .result
        .auction_state
        .bids()
        .find(|(bid_key, _bid)| **bid_key == public_key)
    {
        Some((_, bid)) => {
            let staked_amount = *bid.staked_amount();
            let remainder = staked_amount.saturating_sub(amount);
            if remainder < U512::from(minimum_validator_bid) {
                if !min_bid_override {
                    return Err(CliError::ReducedStakeBelowMinAmount);
                } else {
                    println!("[WARN] Execution of this withdraw bid will result in unbonding of all stake")
                }
            }
        }
        None => return Err(CliError::FailedToGetAuctionState),
    };

    Ok(())
}

async fn check_auction_state_for_withdraw(
    node_address: &str,
    verbosity_level: u64,
    hash_addr: HashAddr,
    entry_point_name: String,
    runtime_args: &RuntimeArgs,
    min_bid_override: bool,
) -> Result<(), CliError> {
    // Best guess on the entry point name
    if entry_point_name == *"withdraw_bid" {
        let state_root_hash = *get_block("", node_address, 0, "")
            .await?
            .result
            .block_with_signatures
            .ok_or_else(|| CliError::FailedToGetStateRootHash)?
            .block
            .state_root_hash();
        let encoded_hash = base16::encode_lower(&state_root_hash);
        let registry =
            crate::cli::get_system_hash_registry(node_address, verbosity_level, &encoded_hash)
                .await?;
        let auction_hash_addr = *registry
            .get("auction")
            .ok_or_else(|| CliError::MissingAuctionHash)?;
        // First check if we are calling the auction.
        if auction_hash_addr == hash_addr {
            // Now parse the args for the amount to do the value check.
        } else {
            // check if the hash addr matches the package hash addr on the contract itself.
            let key = Key::Hash(auction_hash_addr);
            let package_addr = query_global_state(
                "",
                node_address,
                verbosity_level,
                "",
                &encoded_hash,
                &key.to_formatted_string(),
                "",
            )
            .await?
            .result
            .stored_value
            .as_contract()
            .ok_or_else(|| CliError::FailedToGetSystemHashRegistry)?
            .contract_package_hash()
            .value();
            if package_addr != hash_addr {
                return Ok(());
            }
        }
        let amount = runtime_args
            .get("amount")
            .ok_or_else(|| CliError::InvalidCLValue("count not parse amount".to_string()))?
            .to_t::<U512>()
            .map_err(|err| CliError::InvalidCLValue(err.to_string()))?;
        let public_key = runtime_args
            .get("public_key")
            .ok_or_else(|| CliError::InvalidCLValue("count not parse amount".to_string()))?
            .to_t::<PublicKey>()
            .map_err(|err| CliError::InvalidCLValue(err.to_string()))?;
        return do_withdraw_amount_checks(node_address, 0, public_key, amount, min_bid_override)
            .await;
    }
    Ok(())
}

async fn do_deploy_checks(
    node_address: &str,
    min_bid_override: bool,
    deploy: &Deploy,
) -> Result<(), CliError> {
    let session = deploy.session();
    let state_root_hash = *get_block("", node_address, 0, "")
        .await?
        .result
        .block_with_signatures
        .ok_or_else(|| CliError::FailedToGetStateRootHash)?
        .block
        .state_root_hash();
    let encoded_hash = base16::encode_lower(&state_root_hash);
    match session {
        ExecutableDeployItem::ModuleBytes { .. } | ExecutableDeployItem::Transfer { .. } => Ok(()),
        ExecutableDeployItem::StoredContractByHash {
            entry_point,
            hash,
            args,
        } => {
            let hash_addr = hash.value();
            check_auction_state_for_withdraw(
                node_address,
                0,
                hash_addr,
                entry_point.clone(),
                args,
                min_bid_override,
            )
            .await
        }
        ExecutableDeployItem::StoredContractByName {
            name,
            entry_point,
            args,
        } => {
            let account = Key::Account(deploy.account().to_account_hash());
            let cl_value = query_global_state(
                "",
                node_address,
                0,
                "",
                &encoded_hash,
                &account.to_formatted_string(),
                "",
            )
            .await?
            .result
            .stored_value
            .into_account()
            .ok_or_else(|| CliError::InvalidCLValue("unable to parse as cl _value".to_string()))?;
            let key = cl_value.named_keys().get(name);
            match key {
                Some(key) => {
                    let hash_addr = match *key {
                        Key::Hash(addr) => addr,
                        Key::SmartContract(addr) => addr,
                        _ => return Ok(()),
                    };
                    check_auction_state_for_withdraw(
                        node_address,
                        0,
                        hash_addr,
                        entry_point.clone(),
                        args,
                        min_bid_override,
                    )
                    .await
                }
                None => {
                    println!("unable to get named key skipping withdrawal checks");
                    Ok(())
                }
            }
        }
        ExecutableDeployItem::StoredVersionedContractByHash {
            entry_point,
            hash,
            args,
            ..
        } => {
            let hash_addr = hash.value();
            check_auction_state_for_withdraw(
                node_address,
                0,
                hash_addr,
                entry_point.clone(),
                args,
                min_bid_override,
            )
            .await
        }
        ExecutableDeployItem::StoredVersionedContractByName {
            name,
            entry_point,
            args,
            ..
        } => {
            let account = Key::Account(deploy.account().to_account_hash());
            let account = query_global_state(
                "",
                node_address,
                0,
                "",
                &encoded_hash,
                &account.to_formatted_string(),
                "",
            )
            .await?
            .result
            .stored_value
            .into_account()
            .ok_or_else(|| CliError::InvalidCLValue("unable to parse as cl _value".to_string()))?;
            let key = account.named_keys().get(name);
            match key {
                Some(key) => {
                    let hash_addr = match *key {
                        Key::Hash(addr) => addr,
                        Key::SmartContract(addr) => addr,
                        _ => return Ok(()),
                    };
                    check_auction_state_for_withdraw(
                        node_address,
                        0,
                        hash_addr,
                        entry_point.clone(),
                        args,
                        min_bid_override,
                    )
                    .await
                }
                None => {
                    println!("unable to get named key skipping withdrawal checks");
                    Ok(())
                }
            }
        }
    }
}

/// Creates a [`Deploy`] and sends it to the network for execution.
///
/// For details of the parameters, see [the module docs](crate::cli#common-parameters) or the docs
/// of the individual parameter types.
pub async fn put_deploy(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    deploy_params: DeployStrParams<'_>,
    session_params: SessionStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
) -> Result<SuccessResponse<PutDeployResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    let deploy = with_payment_and_session(deploy_params, payment_params, session_params, false)?;
    do_deploy_checks(node_address, true, &deploy).await?;
    #[allow(deprecated)]
    crate::put_deploy(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Creates a [`Deploy`] and sends it to the network for execution.
///
/// For details of the parameters, see [the module docs](crate::cli#common-parameters) or the docs
/// of the individual parameter types.
pub async fn put_deploy_with_min_bid_override(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    min_bid_override: bool,
    deploy_params: DeployStrParams<'_>,
    session_params: SessionStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
) -> Result<SuccessResponse<PutDeployResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    let deploy = with_payment_and_session(deploy_params, payment_params, session_params, false)?;
    if let Err(err) = do_deploy_checks(node_address, min_bid_override, &deploy).await {
        if !min_bid_override {
            return Err(err);
        } else {
            println!("[WARN]: Skipping withdraw bid amount checks: {}", err)
        }
    };
    #[allow(deprecated)]
    crate::put_deploy(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Creates a [`Deploy`] and sends it to the specified node for speculative execution.
///
/// For details of the parameters, see [the module docs](crate::cli#common-parameters) or the docs
/// of the individual parameter types.
pub async fn speculative_put_deploy(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    deploy_params: DeployStrParams<'_>,
    session_params: SessionStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
) -> Result<SuccessResponse<SpeculativeExecResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    let deploy = with_payment_and_session(deploy_params, payment_params, session_params, false)?;
    #[allow(deprecated)]
    crate::speculative_exec(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Returns a [`Deploy`] and outputs it to a file or stdout if the `std-fs-io` feature is enabled.
///
/// As a file, the `Deploy` can subsequently be signed by other parties using [`sign_deploy_file`]
/// and then sent to the network for execution using [`send_deploy_file`].
///
/// If the `std-fs-io` feature is NOT enabled, `maybe_output_path` and `force` are ignored.
/// Otherwise, `maybe_output_path` specifies the output file path, or if empty, will print it to
/// `stdout`.  If `force` is true, and a file exists at `maybe_output_path`, it will be
/// overwritten.  If `force` is false and a file exists at `maybe_output_path`,
/// [`crate::Error::FileAlreadyExists`] is returned and the file will not be written.
pub fn make_deploy(
    #[allow(unused_variables)] maybe_output_path: &str,
    deploy_params: DeployStrParams<'_>,
    session_params: SessionStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
    #[allow(unused_variables)] force: bool,
) -> Result<Deploy, CliError> {
    let deploy = with_payment_and_session(deploy_params, payment_params, session_params, true)?;
    #[cfg(feature = "std-fs-io")]
    {
        let output = parse::output_kind(maybe_output_path, force);
        #[allow(deprecated)]
        crate::output_deploy(output, &deploy).map_err(CliError::from)?;
    }
    Ok(deploy)
}

/// Reads a previously-saved [`Deploy`] from a file, cryptographically signs it, and outputs it to a
/// file or stdout.
///
/// `maybe_output_path` specifies the output file path, or if empty, will print it to `stdout`.  If
/// `force` is true, and a file exists at `maybe_output_path`, it will be overwritten.  If `force`
/// is false and a file exists at `maybe_output_path`, [`crate::Error::FileAlreadyExists`] is returned
/// and the file will not be written.
#[cfg(feature = "std-fs-io")]
pub fn sign_deploy_file(
    input_path: &str,
    secret_key_path: &str,
    maybe_output_path: &str,
    force: bool,
) -> Result<(), CliError> {
    let secret_key = parse::secret_key_from_file(secret_key_path)?;
    let output = parse::output_kind(maybe_output_path, force);
    #[allow(deprecated)]
    crate::sign_deploy_file(input_path, &secret_key, output).map_err(CliError::from)
}

/// Reads a previously-saved [`Deploy`] from a file and sends it to the network for execution.
///
/// For details of the parameters, see [the module docs](crate::cli#common-parameters).
#[cfg(feature = "std-fs-io")]
pub async fn send_deploy_file(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    input_path: &str,
) -> Result<SuccessResponse<PutDeployResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    #[allow(deprecated)]
    let deploy = crate::read_deploy_file(input_path)?;
    #[allow(deprecated)]
    crate::put_deploy(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Reads a previously-saved [`Deploy`] from a file and sends it to the specified node for
/// speculative execution.
/// For details of the parameters, see [the module docs](crate::cli#common-parameters).
#[cfg(feature = "std-fs-io")]
pub async fn speculative_send_deploy_file(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    input_path: &str,
) -> Result<SuccessResponse<SpeculativeExecResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    #[allow(deprecated)]
    let deploy = crate::read_deploy_file(input_path)?;
    #[allow(deprecated)]
    crate::speculative_exec(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Transfers funds between purses.
///
/// * `amount` is a string to be parsed as a `U512` specifying the amount to be transferred.
/// * `target_account` is the [`AccountHash`], [`URef`] or [`PublicKey`] of the account to which the
///   funds will be transferred, formatted as a hex-encoded string.  The account's main purse will
///   receive the funds.
/// * `transfer_id` is a string to be parsed as a `u64` representing a user-defined identifier which
///   will be permanently associated with the transfer.
///
/// For details of other parameters, see [the module docs](crate::cli#common-parameters).
#[allow(clippy::too_many_arguments)]
pub async fn transfer(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    amount: &str,
    target_account: &str,
    transfer_id: &str,
    deploy_params: DeployStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
) -> Result<SuccessResponse<PutDeployResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    let deploy = new_transfer(
        amount,
        None,
        target_account,
        transfer_id,
        deploy_params,
        payment_params,
        false,
    )?;
    #[allow(deprecated)]
    crate::put_deploy(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Creates a [`Deploy`] to transfer funds between purses, and sends it to the specified node for
/// speculative execution.
///
/// * `amount` is a string to be parsed as a `U512` specifying the amount to be transferred.
/// * `target_account` is the [`AccountHash`], [`URef`] or [`PublicKey`] of the account to which the
///   funds will be transferred, formatted as a hex-encoded string.  The account's main purse will
///   receive the funds.
/// * `transfer_id` is a string to be parsed as a `u64` representing a user-defined identifier which
///   will be permanently associated with the transfer.
///
/// For details of other parameters, see [the module docs](crate::cli#common-parameters).
#[allow(clippy::too_many_arguments)]
pub async fn speculative_transfer(
    maybe_rpc_id: &str,
    node_address: &str,
    verbosity_level: u64,
    amount: &str,
    target_account: &str,
    transfer_id: &str,
    deploy_params: DeployStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
) -> Result<SuccessResponse<SpeculativeExecResult>, CliError> {
    let rpc_id = parse::rpc_id(maybe_rpc_id);
    let verbosity = parse::verbosity(verbosity_level);
    let deploy = new_transfer(
        amount,
        None,
        target_account,
        transfer_id,
        deploy_params,
        payment_params,
        false,
    )?;
    #[allow(deprecated)]
    crate::speculative_exec(rpc_id, node_address, verbosity, deploy)
        .await
        .map_err(CliError::from)
}

/// Returns a transfer [`Deploy`] and outputs it to a file or stdout if the `std-fs-io` feature is
/// enabled.
///
/// As a file, the `Deploy` can subsequently be signed by other parties using [`sign_deploy_file`]
/// and then sent to the network for execution using [`send_deploy_file`].
///
/// If the `std-fs-io` feature is NOT enabled, `maybe_output_path` and `force` are ignored.
/// Otherwise, `maybe_output_path` specifies the output file path, or if empty, will print it to
/// `stdout`.  If `force` is true, and a file exists at `maybe_output_path`, it will be
/// overwritten.  If `force` is false and a file exists at `maybe_output_path`,
/// [`crate::Error::FileAlreadyExists`] is returned and the file will not be written.
pub fn make_transfer(
    #[allow(unused_variables)] maybe_output_path: &str,
    amount: &str,
    target_account: &str,
    transfer_id: &str,
    deploy_params: DeployStrParams<'_>,
    payment_params: PaymentStrParams<'_>,
    #[allow(unused_variables)] force: bool,
) -> Result<Deploy, CliError> {
    let deploy = new_transfer(
        amount,
        None,
        target_account,
        transfer_id,
        deploy_params,
        payment_params,
        true,
    )?;
    #[cfg(feature = "std-fs-io")]
    {
        let output = parse::output_kind(maybe_output_path, force);
        #[allow(deprecated)]
        crate::output_deploy(output, &deploy).map_err(CliError::from)?;
    }
    Ok(deploy)
}

/// Creates new Deploy with specified payment and session data.
pub fn with_payment_and_session(
    deploy_params: DeployStrParams,
    payment_params: PaymentStrParams,
    session_params: SessionStrParams,
    allow_unsigned_deploy: bool,
) -> Result<Deploy, CliError> {
    let gas_price: u64 = deploy_params
        .gas_price_tolerance
        .parse::<u64>()
        .unwrap_or(DEFAULT_GAS_PRICE);
    let chain_name = deploy_params.chain_name.to_string();
    let session = parse::session_executable_deploy_item(session_params)?;
    let payment = parse::payment_executable_deploy_item(payment_params)?;
    let timestamp = parse::timestamp(deploy_params.timestamp)?;
    let ttl = parse::ttl(deploy_params.ttl)?;
    let maybe_session_account = parse::session_account(deploy_params.session_account)?;

    let mut deploy_builder = DeployBuilder::new(chain_name, session)
        .with_payment(payment)
        .with_timestamp(timestamp)
        .with_gas_price(gas_price)
        .with_ttl(ttl);
    let maybe_secret_key = get_maybe_secret_key(
        deploy_params.secret_key,
        allow_unsigned_deploy,
        "with_payment_and_session",
    )?;
    if let Some(secret_key) = &maybe_secret_key {
        deploy_builder = deploy_builder.with_secret_key(secret_key);
    }
    if let Some(account) = maybe_session_account {
        deploy_builder = deploy_builder.with_account(account);
    }

    let deploy = deploy_builder.build().map_err(crate::Error::from)?;
    deploy
        .is_valid_size(MAX_SERIALIZED_SIZE_OF_DEPLOY)
        .map_err(crate::Error::from)?;
    Ok(deploy)
}

/// Creates new Transfer with specified data.
pub fn new_transfer(
    amount: &str,
    source_purse: Option<URef>,
    target_account: &str,
    transfer_id: &str,
    deploy_params: DeployStrParams,
    payment_params: PaymentStrParams,
    allow_unsigned_deploy: bool,
) -> Result<Deploy, CliError> {
    let chain_name = deploy_params.chain_name.to_string();
    let payment = parse::payment_executable_deploy_item(payment_params)?;
    let amount = U512::from_dec_str(amount).map_err(|err| CliError::FailedToParseUint {
        context: "new_transfer amount",
        error: UIntParseError::FromDecStr(err),
    })?;

    let target = if let Ok(public_key) = PublicKey::from_hex(target_account) {
        TransferTarget::PublicKey(public_key)
    } else if let Ok(account_hash) = AccountHash::from_formatted_str(target_account) {
        TransferTarget::AccountHash(account_hash)
    } else if let Ok(uref) = URef::from_formatted_str(target_account) {
        TransferTarget::URef(uref)
    } else {
        return Err(CliError::InvalidArgument {
            context: "new_transfer target_account",
            error: format!(
                "allowed types: PublicKey, AccountHash or URef, got {}",
                target_account
            ),
        });
    };

    let transfer_id = parse::transfer_id(transfer_id)?;
    let maybe_transfer_id = Some(transfer_id);

    let timestamp = parse::timestamp(deploy_params.timestamp)?;
    let ttl = parse::ttl(deploy_params.ttl)?;
    let maybe_session_account = parse::session_account(deploy_params.session_account)?;
    let gas_price: u64 = deploy_params
        .gas_price_tolerance
        .parse::<u64>()
        .unwrap_or(DEFAULT_GAS_PRICE);

    let mut deploy_builder =
        DeployBuilder::new_transfer(chain_name, amount, source_purse, target, maybe_transfer_id)
            .with_payment(payment)
            .with_timestamp(timestamp)
            .with_gas_price(gas_price)
            .with_ttl(ttl);

    let maybe_secret_key = get_maybe_secret_key(
        deploy_params.secret_key,
        allow_unsigned_deploy,
        "new_transfer",
    )?;
    if let Some(secret_key) = &maybe_secret_key {
        deploy_builder = deploy_builder.with_secret_key(secret_key);
    }
    if let Some(account) = maybe_session_account {
        deploy_builder = deploy_builder.with_account(account);
    }
    let deploy = deploy_builder.build().map_err(crate::Error::from)?;
    deploy
        .is_valid_size(MAX_SERIALIZED_SIZE_OF_DEPLOY)
        .map_err(crate::Error::from)?;
    Ok(deploy)
}
