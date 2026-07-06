use alloc::collections::BTreeMap;
use casper_types::{
    bytesrepr::{Bytes, ToBytes},
    TransactionArgs, TransactionEntryPoint, TransactionScheduling, TransactionTarget,
};

pub(crate) const ARGS_MAP_KEY: u16 = 0;
pub(crate) const TARGET_MAP_KEY: u16 = 1;
pub(crate) const ENTRY_POINT_MAP_KEY: u16 = 2;
pub(crate) const SCHEDULING_MAP_KEY: u16 = 3;

#[derive(Clone, Eq, PartialEq, Debug)]
pub(crate) enum FieldsContainerError {
    CouldNotSerializeField { field_index: u16 },
}

pub(crate) struct FieldsContainer {
    pub(super) args: TransactionArgs,
    pub(super) target: TransactionTarget,
    pub(super) entry_point: TransactionEntryPoint,
    pub(super) scheduling: TransactionScheduling,
}

impl FieldsContainer {
    pub(crate) fn new(
        args: TransactionArgs,
        target: TransactionTarget,
        entry_point: TransactionEntryPoint,
        scheduling: TransactionScheduling,
    ) -> Self {
        FieldsContainer {
            args,
            target,
            entry_point,
            scheduling,
        }
    }

    pub(crate) fn to_map(&self) -> Result<BTreeMap<u16, Bytes>, FieldsContainerError> {
        let mut map: BTreeMap<u16, Bytes> = BTreeMap::new();
        map.insert(
            ARGS_MAP_KEY,
            self.args.to_bytes().map(Into::into).map_err(|_| {
                FieldsContainerError::CouldNotSerializeField {
                    field_index: ARGS_MAP_KEY,
                }
            })?,
        );
        map.insert(
            TARGET_MAP_KEY,
            self.target.to_bytes().map(Into::into).map_err(|_| {
                FieldsContainerError::CouldNotSerializeField {
                    field_index: TARGET_MAP_KEY,
                }
            })?,
        );
        map.insert(
            ENTRY_POINT_MAP_KEY,
            self.entry_point.to_bytes().map(Into::into).map_err(|_| {
                FieldsContainerError::CouldNotSerializeField {
                    field_index: ENTRY_POINT_MAP_KEY,
                }
            })?,
        );
        map.insert(
            SCHEDULING_MAP_KEY,
            self.scheduling.to_bytes().map(Into::into).map_err(|_| {
                FieldsContainerError::CouldNotSerializeField {
                    field_index: SCHEDULING_MAP_KEY,
                }
            })?,
        );
        Ok(map)
    }
}
