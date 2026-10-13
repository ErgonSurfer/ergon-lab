// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Synthetic fixtures/oracles only. Actual upstream ALP builders and protobuf
//! types, never another parser, token validator, indexer or HTTP handler.
use abc_rust_error::{bail, Result};
use bitcoinsuite_core::tx::TxId;
use bitcoinsuite_slp::{
    alp::{burn_section, genesis_section, mint_section, sections_opreturn, send_section},
    parsed::ParsedMintData,
    structs::GenesisInfo,
    token_id::TokenId,
    token_type::AlpTokenType,
};
use chronik_proto::proto;
use prost::Message;

pub(crate) fn alp_script(op: u8, id: [u8; 32]) -> Result<Vec<u8>> {
    let id = TokenId::new(TxId::new(id));
    let section = match op {
        0 => genesis_section(
            AlpTokenType::Standard,
            &GenesisInfo {
                token_ticker: b"ELTEST".as_slice().into(),
                token_name: b"Ergon Lab synthetic fixture".as_slice().into(),
                decimals: 0,
                ..Default::default()
            },
            &ParsedMintData {
                atoms_vec: vec![1000],
                num_batons: 1,
            },
        ),
        1 => mint_section(
            &id,
            AlpTokenType::Standard,
            &ParsedMintData {
                atoms_vec: vec![500],
                num_batons: 1,
            },
        ),
        2 => send_section(&id, AlpTokenType::Standard, [1400, 100]),
        3 => burn_section(&id, AlpTokenType::Standard, 100),
        _ => bail!("Unknown synthetic ALP operation"),
    };
    Ok(sections_opreturn(vec![section]).bytecode().to_vec())
}

fn token_type(typ: &Option<proto::TokenType>) -> bool {
    typ.as_ref().and_then(|t| t.token_type.as_ref()) == Some(&proto::token_type::TokenType::Alp(0))
}
fn height(tx: &proto::Tx) -> i32 {
    tx.block.as_ref().map_or(-1, |b| b.height)
}
fn expected_height(value: u64) -> Result<i32> {
    if value == u64::MAX {
        return Ok(-1);
    }
    Ok(i32::try_from(value)?)
}
fn flat_ids(txs: &[proto::Tx]) -> Vec<u8> {
    txs.iter().flat_map(|tx| tx.txid.clone()).collect()
}
fn ordered_points(mut actual: Vec<Vec<u8>>, expected: &[u8]) -> bool {
    if expected.len() % 36 != 0 {
        return false;
    }
    let mut expected: Vec<_> = expected.chunks_exact(36).map(<[u8]>::to_vec).collect();
    actual.sort();
    expected.sort();
    actual == expected
}
fn point(out: &Option<proto::OutPoint>) -> Vec<u8> {
    out.as_ref().map_or(Vec::new(), |o| {
        let mut p = o.txid.clone();
        p.extend_from_slice(&o.out_idx.to_le_bytes());
        p
    })
}
/// Private fixture discriminator, not a new public protocol or handler.
/// Oracle values are supplied from the actual native accepted block/tx bytes.
pub(crate) fn check(kind: u8, body: &[u8], ids: &[u8], values: &[u64]) -> Result<bool> {
    let required = match kind {
        1 => 2,
        2 => 1,
        5 => 3,
        7 => 2,
        8 | 9 => 4,
        0 | 3 | 4 | 6 | 10 => 0,
        _ => bail!("Unknown HTTP fixture oracle"),
    };
    if values.len() != required {
        bail!("Wrong HTTP fixture oracle arity");
    }
    let v = |i: usize| {
        values
            .get(i)
            .copied()
            .ok_or_else(|| abc_rust_error::Report::msg("Missing fixture value"))
    };
    Ok(match kind {
        0 => proto::ChronikInfo::decode(body)?.version.as_bytes() == ids,
        1 => {
            let b = proto::Block::decode(body)?.block_info;
            b.is_some_and(|b| {
                b.hash == ids
                    && b.height as u64 == values[0]
                    && b.num_txs == values[1]
                    && b.sum_coinbase_output_sats == 0
                    && !b.is_final
            })
        }
        2 => {
            let blocks = proto::Blocks::decode(body)?.blocks;
            blocks
                .iter()
                .flat_map(|b| b.hash.clone())
                .collect::<Vec<_>>()
                == ids
                && blocks
                    .iter()
                    .enumerate()
                    .all(|(i, b)| b.height as u64 == values[0] + i as u64 && !b.is_final)
        }
        3 => {
            let b = proto::BlockHeader::decode(body)?;
            b.raw_header == ids && b.root.is_empty() && b.branch.is_empty()
        }
        4 => {
            let h = proto::BlockHeaders::decode(body)?.headers;
            h.iter()
                .flat_map(|b| b.raw_header.clone())
                .collect::<Vec<_>>()
                == ids
                && h.iter()
                    .all(|b| b.raw_header.len() == 80 && b.root.is_empty() && b.branch.is_empty())
        }
        5 => {
            let page = proto::TxHistoryPage::decode(body)?;
            let mut actual = flat_ids(&page.txs);
            let mut wanted = ids.to_vec();
            if v(2)? == 0 {
                let mut a: Vec<_> = actual.chunks_exact(32).map(<[u8]>::to_vec).collect();
                let mut b: Vec<_> = wanted.chunks_exact(32).map(<[u8]>::to_vec).collect();
                a.sort();
                b.sort();
                actual = a.concat();
                wanted = b.concat();
            }
            actual == wanted
                && page.num_txs as u64 == v(0)?
                && page.num_pages as u64 == v(1)?
                && ids.len() % 32 == 0
                && page.txs.iter().all(|t| !t.is_final)
        }
        6 => proto::RawTx::decode(body)?.raw_tx == ids,
        7 => {
            let info = proto::TokenInfo::decode(body)?;
            let expected = TxId::new(ids.try_into()?).to_string();
            info.token_id == expected
                && token_type(&info.token_type)
                && info.block.as_ref().map_or(-1, |b| b.height) == expected_height(v(0)?)?
                && info.genesis_info.is_some_and(|g| {
                    g.token_ticker == b"ELTEST"
                        && g.token_name == b"Ergon Lab synthetic fixture"
                        && g.decimals == 0
                })
                && info.time_first_seen == i64::try_from(v(1)?)?
        }
        8 => {
            let response = proto::ScriptUtxos::decode(body)?;
            let bound_height = expected_height(v(3)?)?;
            let utxos = response.utxos;
            response.script == [0x51]
                && utxos.len() as u64 == v(0)?
                && utxos
                    .iter()
                    .map(|o| o.token.as_ref().map_or(0, |t| t.atoms))
                    .sum::<u64>()
                    == v(1)?
                && utxos
                    .iter()
                    .filter(|o| o.token.as_ref().is_some_and(|t| t.is_mint_baton))
                    .count() as u64
                    == v(2)?
                && utxos.iter().all(|o| {
                    o.sats == 0 && !o.is_final && !o.is_coinbase && o.block_height == bound_height
                })
                && ordered_points(utxos.iter().map(|o| point(&o.outpoint)).collect(), ids)
        }
        9 => {
            let bound_height = expected_height(v(3)?)?;
            let utxos = proto::Utxos::decode(body)?.utxos;
            utxos.len() as u64 == v(0)?
                && utxos
                    .iter()
                    .map(|o| o.token.as_ref().map_or(0, |t| t.atoms))
                    .sum::<u64>()
                    == v(1)?
                && utxos
                    .iter()
                    .filter(|o| o.token.as_ref().is_some_and(|t| t.is_mint_baton))
                    .count() as u64
                    == v(2)?
                && utxos.iter().all(|o| {
                    o.sats == 0
                        && !o.is_final
                        && !o.is_coinbase
                        && o.block_height == bound_height
                        && o.token.as_ref().is_some_and(|t| token_type(&t.token_type))
                })
                && ordered_points(utxos.iter().map(|o| point(&o.outpoint)).collect(), ids)
        }
        10 => proto::Error::decode(body)?.msg.as_bytes().starts_with(ids),
        _ => bail!("Unknown HTTP fixture oracle"),
    })
}

pub(crate) fn alp_tx(
    body: &[u8],
    id: [u8; 32],
    token: [u8; 32],
    kind: i32,
    atoms: u64,
    burn: u64,
    invalid: bool,
    expected_height: i32,
    time: i64,
) -> Result<bool> {
    let tx = proto::Tx::decode(body)?;
    let token = TxId::new(token).to_string();
    if tx.txid != id
        || height(&tx) != expected_height
        || tx.token_entries.len() != 1
        || tx.time_first_seen != time
        || tx.is_coinbase
        || tx.is_final
        || !tx.token_failed_parsings.is_empty()
    {
        return Ok(false);
    }
    let entry = &tx.token_entries[0];
    let outputs: Vec<_> = tx.outputs.iter().filter_map(|o| o.token.as_ref()).collect();
    let expected_batons = usize::from(!invalid && (kind == 2 || kind == 4));
    // Bind each coloring, not just a sum that could hide swapped outputs or
    // incorrect ancestry. All expectations describe this one synthetic cycle.
    let (inputs, output_colors) = match (kind, invalid) {
        (2, false) => (vec![None], vec![None, Some((1000, false)), Some((0, true))]),
        (4, false) => (
            vec![Some((0, true))],
            vec![None, Some((500, false)), Some((0, true))],
        ),
        (3, false) => (
            vec![Some((1000, false)), Some((500, false))],
            vec![None, Some((1400, false)), Some((100, false))],
        ),
        (5, false) => (vec![Some((100, false))], vec![None, None]),
        (4, true) => (vec![Some((1000, false))], vec![None, None, None]),
        _ => return Ok(false),
    };
    let matches =
        |actual: &Option<proto::Token>, expected: &Option<(u64, bool)>| match (actual, expected) {
            (None, None) => true,
            (Some(t), Some((atoms, baton))) => {
                t.token_id == token
                    && token_type(&t.token_type)
                    && t.entry_idx == 0
                    && t.atoms == *atoms
                    && t.is_mint_baton == *baton
            }
            _ => false,
        };
    Ok(entry.token_id == token
        && token_type(&entry.token_type)
        && entry.tx_type == kind
        && entry.is_invalid == invalid
        && entry.actual_burn_atoms == burn.to_string()
        && entry.intentional_burn_atoms == if kind == 5 { burn } else { 0 }
        && !entry.burns_mint_batons
        && tx.token_status
            == if invalid {
                proto::TokenStatus::NotNormal as i32
            } else {
                proto::TokenStatus::Normal as i32
            }
        && outputs.iter().map(|t| t.atoms).sum::<u64>() == atoms
        && outputs.iter().filter(|t| t.is_mint_baton).count() == expected_batons
        && outputs
            .iter()
            .all(|t| t.token_id == token && token_type(&t.token_type))
        && tx.inputs.len() == inputs.len()
        && tx
            .inputs
            .iter()
            .zip(&inputs)
            .all(|(i, t)| matches(&i.token, t))
        && tx.outputs.len() == output_colors.len()
        && tx
            .outputs
            .iter()
            .zip(&output_colors)
            .all(|(o, t)| matches(&o.token, t))
        && tx.inputs.iter().all(|i| i.sats == 0)
        && tx.outputs.iter().all(|o| o.sats == 0))
}

pub(crate) fn spent(body: &[u8], output: u32, spender: [u8; 32], input: u32) -> Result<bool> {
    let tx = proto::Tx::decode(body)?;
    let Some(o) = tx.outputs.get(output as usize) else {
        return Ok(false);
    };
    Ok(o.spent_by
        .as_ref()
        .is_some_and(|s| s.txid == spender && s.input_idx == input))
}

pub(crate) fn parse_failure(body: &[u8], id: [u8; 32]) -> Result<bool> {
    let tx = proto::Tx::decode(body)?;
    Ok(tx.txid == id
        && tx.token_status == proto::TokenStatus::NotNormal as i32
        && !tx.token_failed_parsings.is_empty()
        && tx.token_entries.is_empty()
        && tx.outputs.iter().all(|o| o.token.is_none())
        && tx.inputs.iter().all(|i| i.token.is_none()))
}
