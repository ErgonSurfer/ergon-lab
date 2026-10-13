// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <algorithm>
#include <chain.h>
#include <chainparams.h>
#include <chronik-cpp/ergon_node_access.h>
#include <chronik-cpp/ergon_position_reader.h>
#include <clientversion.h>
#include <consensus/merkle.h>
#include <flatfile.h>
#include <hash.h>
#include <limits>
#include <map>
#include <mutex>
#include <shutdown.h>
#include <stdexcept>
#include <streams.h>
#include <undo.h>
#include <util/system.h>
#include <validation.h>
#include <version.h>

namespace chronik_bridge {
namespace {
// Index-service budgets, not native block/transaction validity limits.
constexpr uint32_t PAYLOAD_LIMIT = 64 * 1024 * 1024;
constexpr size_t TRANSACTION_LIMIT = 65536;
constexpr size_t LAYOUT_LIMIT = 8;
using Key = std::pair<int, uint32_t>;
struct Reference {
    int height;
    BlockHash hash;
    FlatFilePos data, undo;
    BlockHash parent;
    uint64_t generation;
};
struct Span {
    uint32_t position, size;
    size_t ordinal;
    TxId id;
};
struct Layout {
    BlockHash hash;
    uint32_t payload_size;
    std::map<uint32_t, Span> spans;
};
uint32_t Position(uint64_t value) {
    if (value > std::numeric_limits<uint32_t>::max())
        throw std::runtime_error("Position exceeds native ABI");
    return uint32_t(value);
}
std::vector<uint8_t> ReadBytes(FlatFilePos pos, uint32_t length, bool undo) {
    if (pos.IsNull() || length > PAYLOAD_LIMIT + 32 ||
        uint64_t(pos.nPos) + length > std::numeric_limits<uint32_t>::max())
        throw std::runtime_error("Invalid bounded native read");
    // Same native flat-file naming/seek primitive. The undo opener itself is
    // private in this baseline; no native source export or ABC host import.
    // Positive chunk size is required by FlatFileSeq even for Open-only use;
    // no allocation/flush operation is performed by this reader.
    CAutoFile file(undo ? FlatFileSeq(GetBlocksDir(), "rev", 1).Open(pos, true)
                        : OpenBlockFile(pos, true),
                   SER_DISK, CLIENT_VERSION);
    if (file.IsNull())
        throw std::runtime_error("Native positioned file unavailable");
    std::vector<uint8_t> bytes(length);
    file.read(reinterpret_cast<char *>(bytes.data()), bytes.size());
    return bytes;
}
uint32_t Envelope(FlatFilePos pos, bool undo) {
    if (pos.nPos < 8)
        throw std::runtime_error("Invalid native record envelope position");
    pos.nPos -= 8;
    auto bytes = ReadBytes(pos, 8, undo);
    CDataStream stream(bytes, SER_DISK, CLIENT_VERSION);
    CMessageHeader::MessageMagic magic;
    uint32_t size;
    stream >> magic >> size;
    if (magic != Params().DiskMagic() || size == 0 || size > PAYLOAD_LIMIT)
        throw std::runtime_error("Native record envelope or budget mismatch");
    return size;
}
std::shared_ptr<const Layout> LoadLayout(const Reference &ref) {
    const auto size = Envelope(ref.data, false);
    auto bytes = ReadBytes(ref.data, size, false);
    CDataStream count(bytes, SER_DISK, CLIENT_VERSION);
    CBlockHeader header;
    count >> header;
    const auto num_txs = ReadCompactSize(count);
    if (!num_txs || num_txs > TRANSACTION_LIMIT)
        throw std::runtime_error("Native transaction layout budget exceeded");
    CDataStream stream(bytes, SER_DISK, CLIENT_VERSION);
    CBlock block;
    stream >> block;
    bool mutated = false;
    if (!stream.empty() || block.GetHash() != ref.hash ||
        BlockMerkleRoot(block, &mutated) != block.hashMerkleRoot || mutated ||
        block.vtx.size() != num_txs || !block.vtx.front()->IsCoinBase() ||
        ::GetSerializeSize(block, CLIENT_VERSION) != size)
        throw std::runtime_error("Native block body/layout identity mismatch");
    auto layout = std::make_shared<Layout>();
    layout->hash = ref.hash;
    layout->payload_size = size;
    uint64_t offset =
        uint64_t(ref.data.nPos) + 80 + GetSizeOfCompactSize(num_txs);
    for (size_t i = 0; i < block.vtx.size(); ++i) {
        const auto tx_size = ::GetSerializeSize(*block.vtx[i], CLIENT_VERSION);
        if ((i != 0 && block.vtx[i]->IsCoinBase()) ||
            offset + tx_size > uint64_t(ref.data.nPos) + size)
            throw std::runtime_error("Invalid native transaction boundary");
        const auto pos = Position(offset);
        if (!layout->spans
                 .emplace(pos, Span{pos, Position(tx_size), i,
                                    block.vtx[i]->GetId()})
                 .second)
            throw std::runtime_error("Ambiguous native transaction boundary");
        offset += tx_size;
    }
    if (offset != uint64_t(ref.data.nPos) + size)
        throw std::runtime_error("Incomplete native transaction layout");
    return layout;
}
std::vector<::Coin> ReadUndo(const Reference &ref, const Layout &layout,
                             const Span &span, uint32_t expected,
                             uint64_t &read_bytes) {
    if (span.ordinal == 0) {
        if (expected != 0)
            throw std::runtime_error("Coinbase undo position mismatch");
        return {};
    }
    const auto size = Envelope(ref.undo, true);
    auto bytes = ReadBytes(ref.undo, size + 32, true);
    read_bytes = bytes.size() + 8;
    CDataStream count(bytes, SER_DISK, CLIENT_VERSION);
    const auto num_undo = ReadCompactSize(count);
    if (num_undo >= TRANSACTION_LIMIT || num_undo + 1 != layout.spans.size())
        throw std::runtime_error("Native undo count/budget mismatch");
    CDataStream stream(bytes, SER_DISK, CLIENT_VERSION);
    CHashVerifier<CDataStream> verifier(&stream);
    verifier << ref.parent;
    CBlockUndo undo;
    verifier >> undo;
    uint256 checksum;
    stream >> checksum;
    if (!stream.empty() || checksum != verifier.GetHash() ||
        undo.vtxundo.size() + 1 != layout.spans.size() ||
        ::GetSerializeSize(undo, CLIENT_VERSION) != size)
        throw std::runtime_error("Native undo checksum/cardinality mismatch");
    uint64_t offset =
        uint64_t(ref.undo.nPos) + GetSizeOfCompactSize(undo.vtxundo.size());
    for (size_t i = 0; i + 1 < span.ordinal; ++i)
        offset += ::GetSerializeSize(undo.vtxundo[i], CLIENT_VERSION);
    if (Position(offset) != expected)
        throw std::runtime_error("Native transaction undo boundary mismatch");
    return undo.vtxundo[span.ordinal - 1].vprevout;
}
} // namespace

struct NativePositionReader::Impl {
    const ErgonNodeAccess &node;
    mutable std::mutex mutex;
    std::map<Key, int> directory;
    std::vector<Key> heights;
    BlockHash last_tip;
    uint64_t generation{0};
    std::map<Key, std::shared_ptr<const Layout>> layouts;
    NativePositionStats stats;
    std::function<void()> before_read;
    explicit Impl(const ErgonNodeAccess &access) : node(access) {}

    // Lock order is native cs_main -> metadata mutex, never the reverse.
    // No disk read/DB work occurs under this catalogue fence. Initial native
    // metadata bootstrap is O(height); later work is O(changed suffix).
    Reference Resolve(uint32_t file, uint32_t position) {
        if (file > uint32_t(std::numeric_limits<int>::max()))
            throw std::runtime_error("Invalid native file number");
        LOCK(cs_main);
        if (fPruneMode || ShutdownRequested() || node.index_failed.load() ||
            !ChainActive().Tip() || !ChainActive().GetSourceGeneration())
            throw std::runtime_error("Native position source unavailable");
        std::lock_guard<std::mutex> lock(mutex);
        const auto current = ChainActive().GetSourceGeneration();
        if (generation != current) {
            int fork_height = -1;
            auto old = mapBlockIndex.find(last_tip);
            if (generation && old != mapBlockIndex.end()) {
                const auto *fork = ChainActive().FindFork(old->second);
                if (fork)
                    fork_height = fork->nHeight;
            }
            while (heights.size() > size_t(fork_height + 1)) {
                const auto key = heights.back();
                if (key.first >= 0)
                    directory.erase(key);
                layouts.erase(key);
                heights.pop_back();
                ++stats.catalogue_removed;
            }
            for (int h = int(heights.size()); h <= ChainActive().Height();
                 ++h) {
                const auto pos = ChainActive()[h]->GetBlockPos();
                const Key key{pos.nFile, pos.nPos};
                if (!pos.IsNull() && !directory.emplace(key, h).second)
                    throw std::runtime_error(
                        "Ambiguous native storage catalogue");
                heights.push_back(key);
                ++stats.catalogue_added;
            }
            last_tip = ChainActive().Tip()->GetBlockHash();
            generation = current;
        }
        auto found = directory.upper_bound({int(file), position});
        if (found == directory.begin() || (--found)->first.first != int(file))
            throw std::runtime_error("Position outside active native storage");
        const auto *index = ChainActive()[found->second];
        if (!index || index->GetBlockPos() !=
                          FlatFilePos(found->first.first, found->first.second))
            throw std::runtime_error(
                "Native catalogue storage identity changed");
        return {index->nHeight,
                index->GetBlockHash(),
                index->GetBlockPos(),
                index->GetUndoPos(),
                index->pprev ? index->pprev->GetBlockHash() : BlockHash{},
                current};
    }
    void Check(const Reference &ref) const {
        LOCK(cs_main);
        const auto *index = ChainActive()[ref.height];
        if (fPruneMode || ShutdownRequested() || node.index_failed.load() ||
            ChainActive().GetSourceGeneration() != ref.generation || !index ||
            index->GetBlockHash() != ref.hash ||
            index->GetBlockPos() != ref.data || index->GetUndoPos() != ref.undo)
            throw std::runtime_error(
                "Native position source changed during read");
    }
};
NativePositionReader::NativePositionReader(const ErgonNodeAccess &node)
    : m_impl(std::make_unique<Impl>(node)) {}
NativePositionReader::~NativePositionReader() = default;
PositionedTransaction
NativePositionReader::Read(uint32_t file, uint32_t position,
                           const uint32_t *expected_undo) const {
    const auto ref = m_impl->Resolve(file, position);
    const Key key{ref.data.nFile, ref.data.nPos};
    std::shared_ptr<const Layout> layout;
    std::function<void()> hook;
    {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        auto found = m_impl->layouts.find(key);
        if (found != m_impl->layouts.end() && found->second->hash == ref.hash)
            layout = found->second;
        hook = m_impl->before_read;
    }
    if (hook)
        hook();
    if (!layout) {
        layout = LoadLayout(ref);
        m_impl->Check(ref);
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        m_impl->layouts[key] = layout;
        while (m_impl->layouts.size() > LAYOUT_LIMIT)
            m_impl->layouts.erase(m_impl->layouts.begin());
        ++m_impl->stats.cold_layouts;
        m_impl->stats.cold_block_bytes += layout->payload_size + 8;
    } else {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        ++m_impl->stats.warm_reads;
    }
    const auto found = layout->spans.find(position);
    if (found == layout->spans.end())
        throw std::runtime_error(
            "Position is not a native transaction boundary");
    const auto &span = found->second;
    if (Envelope(ref.data, false) != layout->payload_size)
        throw std::runtime_error("Native block envelope changed");
    auto header_bytes = ReadBytes(ref.data, 80, false);
    CDataStream header_stream(header_bytes, SER_DISK, CLIENT_VERSION);
    CBlockHeader header;
    header_stream >> header;
    if (header.GetHash() != ref.hash)
        throw std::runtime_error("Native block header changed");
    auto bytes = ReadBytes(FlatFilePos(int(file), position), span.size, false);
    CDataStream stream(bytes, SER_DISK, CLIENT_VERSION);
    CTransactionRef tx;
    stream >> tx;
    if (!stream.empty() || !tx || tx->GetId() != span.id ||
        ::GetSerializeSize(*tx, CLIENT_VERSION) != span.size)
        throw std::runtime_error(
            "Native positioned transaction identity mismatch");
    uint64_t undo_bytes = 0;
    auto coins = expected_undo
                     ? ReadUndo(ref, *layout, span, *expected_undo, undo_bytes)
                     : std::vector<::Coin>{};
    m_impl->Check(ref);
    {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        m_impl->stats.transaction_bytes += bytes.size();
        m_impl->stats.undo_bytes += undo_bytes;
    }
    return {std::move(tx), std::move(coins)};
}
NativePositionStats NativePositionReader::Stats() const {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    auto stats = m_impl->stats;
    stats.cached_layouts = m_impl->layouts.size();
    return stats;
}
void NativePositionReader::BeforeReadForTesting(std::function<void()> hook) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    m_impl->before_read = std::move(hook);
}
} // namespace chronik_bridge
