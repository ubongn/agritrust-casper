use async_trait::async_trait;
use clap::{ArgMatches, Command};
use serde::{Deserialize, Serialize};

use casper_client::cli::CliError;
use casper_types::{
    ActivationPoint, CoreConfig, HighwayConfig, ProtocolVersion, StorageCosts, SystemConfig,
    TransactionConfig, VacancyConfig, WasmConfig,
};

use super::creation_common::{
    activate_bid, add_bid, add_reservations, cancel_reservations, change_bid_public_key, delegate,
    invocable_entity, invocable_entity_alias, package, package_alias, public_key, redelegate,
    session, transfer, undelegate, withdraw_bid, withdraw_bid_all,
};

use crate::{command::ClientCommand, common, Success};

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

pub struct PutTransaction;
const ALIAS: &str = "put-txn";
#[async_trait]
impl ClientCommand for PutTransaction {
    const NAME: &'static str = "put-transaction";

    const ABOUT: &'static str = "Create a transaction and send it to the network for execution";

    fn build(display_order: usize) -> Command {
        Command::new(Self::NAME)
            .about(Self::ABOUT)
            .alias(ALIAS)
            .subcommand_required(true)
            .subcommand(add_bid::put_transaction_build())
            .subcommand(activate_bid::put_transaction_build())
            .subcommand(withdraw_bid_all::put_transaction_build())
            .subcommand(withdraw_bid::put_transaction_build())
            .subcommand(delegate::put_transaction_build())
            .subcommand(undelegate::put_transaction_build())
            .subcommand(redelegate::put_transaction_build())
            .subcommand(change_bid_public_key::put_transaction_build())
            .subcommand(add_reservations::put_transaction_build())
            .subcommand(cancel_reservations::put_transaction_build())
            .subcommand(invocable_entity::put_transaction_build())
            .subcommand(invocable_entity_alias::put_transaction_build())
            .subcommand(package::put_transaction_build())
            .subcommand(package_alias::put_transaction_build())
            .subcommand(session::put_transaction_build())
            .subcommand(transfer::put_transaction_build())
            .display_order(display_order)
    }

    async fn run(matches: &ArgMatches) -> Result<Success, CliError> {
        match matches.subcommand() {
            None => Err(CliError::InvalidArgument {
                context: "Make Transaction",
                error: "failure to provide recognized subcommand".to_string(),
            }),
            Some((subcommand, arg_matches)) => match subcommand {
                add_bid::NAME => put_add_bid_transaction(arg_matches).await,
                activate_bid::NAME => put_activate_bid_transaction(arg_matches).await,
                withdraw_bid_all::NAME => put_withdraw_all_transaction(arg_matches).await,
                withdraw_bid::NAME => put_withdraw_bid_transaction(arg_matches).await,
                delegate::NAME => put_delegate_transaction(arg_matches).await,
                undelegate::NAME => put_undelegate_transaction(arg_matches).await,
                redelegate::NAME => put_redelegate_transaction(arg_matches).await,
                change_bid_public_key::NAME => put_change_public_key_transaction(arg_matches).await,
                add_reservations::NAME => put_add_reservations_transaction(arg_matches).await,
                cancel_reservations::NAME => put_cancel_reservations_transaction(arg_matches).await,
                invocable_entity::NAME => put_entity_by_hash_transaction(arg_matches).await,
                invocable_entity_alias::NAME => put_entity_by_name_transaction(arg_matches).await,
                package::NAME => put_by_package_hash_transaction(arg_matches).await,
                package_alias::NAME => put_package_by_name_transaction(arg_matches).await,
                session::NAME => put_session_transaction(arg_matches).await,
                transfer::NAME => put_transfer_transaction(arg_matches).await,
                _ => {
                    return Err(CliError::InvalidArgument {
                        context: "Make Transaction",
                        error: "failure to provide recognized subcommand".to_string(),
                    })
                }
            },
        }
    }
}

async fn put_add_bid_transaction(matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(matches);
    let rpc_id = common::rpc_id::get(matches);
    let verbosity_level = common::verbose::get(matches);

    let (transaction_builder_params, transaction_str_params) = add_bid::run(matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_activate_bid_transaction(matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(matches);
    let rpc_id = common::rpc_id::get(matches);
    let verbosity_level = common::verbose::get(matches);

    let (transaction_builder_params, transaction_str_params) = activate_bid::run(matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_withdraw_all_transaction(matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(matches);
    let rpc_id = common::rpc_id::get(matches);
    let verbosity_level = common::verbose::get(matches);

    let public_key_str = public_key::get(matches)?;
    let public_key = public_key::parse_public_key(&public_key_str)?;

    let (transaction_builder_params, transaction_str_params) =
        match casper_client::cli::get_auction_info("", node_address, verbosity_level, "")
            .await?
            .result
            .auction_state
            .bids()
            .find(|(bid_key, _bid)| **bid_key == public_key)
        {
            Some((_, bid)) => {
                let staked_amount = *bid.staked_amount();
                withdraw_bid_all::run(matches, staked_amount)?
            }
            None => return Err(CliError::FailedToGetAuctionState),
        };

    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_withdraw_bid_transaction(matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(matches);
    let rpc_id = common::rpc_id::get(matches);
    let verbosity_level = common::verbose::get(matches);

    let (transaction_builder_params, transaction_str_params) = withdraw_bid::run(matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_delegate_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = delegate::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_undelegate_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = undelegate::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_redelegate_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = redelegate::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_change_public_key_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) =
        change_bid_public_key::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_add_reservations_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = add_reservations::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}
async fn put_cancel_reservations_transaction(
    arg_matches: &ArgMatches,
) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) =
        cancel_reservations::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_entity_by_hash_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = invocable_entity::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_entity_by_name_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) =
        invocable_entity_alias::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_by_package_hash_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = package::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_package_by_name_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = package_alias::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_session_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = session::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}

async fn put_transfer_transaction(arg_matches: &ArgMatches) -> Result<Success, CliError> {
    let node_address = common::node_address::get(arg_matches);
    let rpc_id = common::rpc_id::get(arg_matches);
    let verbosity_level = common::verbose::get(arg_matches);

    let (transaction_builder_params, transaction_str_params) = transfer::run(arg_matches)?;
    casper_client::cli::put_transaction(
        rpc_id,
        node_address,
        verbosity_level,
        transaction_builder_params,
        transaction_str_params,
    )
    .await
    .map(Success::from)
}
