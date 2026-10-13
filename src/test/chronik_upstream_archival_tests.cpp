// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
// Bounded native/HTTP/ALP fixtures, NOT production-service or cutover evidence.
#include <algorithm>
#include <atomic>
#include <boost/asio.hpp>
#include <boost/test/tree/global_fixture.hpp>
#include <boost/test/unit_test.hpp>
#include <chainparams.h>
#include <chronik-bridge/src/ffi.rs.h>
#include <chronik-cpp/chronik_bridge.h>
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_node_access.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <clientversion.h>
#include <condition_variable>
#include <config.h>
#include <consensus/validation.h>
#include <fs.h>
#include <fstream>
#include <future>
#include <iostream>
#include <mutex>
#include <script/sign.h>
#include <shutdown.h>
#include <sstream>
#include <streams.h>
#include <test/setup_common.h>
#include <thread>
#include <txdb.h>
#include <txmempool.h>
#include <util/source_generation.h>
#include <util/system.h>
#include <util/time.h>
#include <validation.h>
#include <validationinterface.h>
#include <version.h>

namespace {
// Stable, flushed progress names: no clocks, paths or normalization. A killed
// process leaves the exact last started case visible, unlike buffered XML.
// This translation unit is compiled only in the archival fixture profile.
struct FixtureProgress : boost::unit_test::global_configuration {
    void test_unit_start(const boost::unit_test::test_unit &unit) override {
        if (unit.p_type == boost::unit_test::TUT_CASE)
            std::cerr << "Fixture start: " << unit.full_name() << std::endl;
    }
    void test_unit_finish(const boost::unit_test::test_unit &unit,
                          unsigned long) override {
        if (unit.p_type == boost::unit_test::TUT_CASE)
            std::cerr << "Fixture done: " << unit.full_name() << std::endl;
    }
} fixture_progress;
template <typename Hash> std::array<uint8_t, 32> Bytes(const Hash &hash) {
    std::array<uint8_t, 32> out;
    std::copy_n(hash.begin(), out.size(), out.begin());
    return out;
}
std::string Root(const std::string &name) {
    const auto path = GetDataDir() / name;
    BOOST_REQUIRE(fs::create_directory(path));
    // macOS /var is a symlink: hand the adapter the canonical fixture path.
    return fs::canonical(path).string();
}
struct CallbackLatch {
    std::mutex mu;
    std::condition_variable cv;
    bool entered{false}, released{false};
    void Block() {
        std::unique_lock<std::mutex> lock(mu);
        entered = true;
        cv.notify_all();
        if (!cv.wait_for(lock, std::chrono::seconds(5),
                         [&] { return released; }))
            throw std::runtime_error("Fixture callback latch timed out");
    }
    bool Wait() {
        std::unique_lock<std::mutex> lock(mu);
        return cv.wait_for(lock, std::chrono::seconds(5),
                           [&] { return entered; });
    }
    void Release() {
        std::lock_guard<std::mutex> lock(mu);
        released = true;
        cv.notify_all();
    }
};
struct DataPositionRestore {
    int height;
    unsigned int original;
    explicit DataPositionRestore(int value) : height(value) {
        LOCK(cs_main);
        original = ChainActive()[height]->nDataPos;
    }
    ~DataPositionRestore() {
        LOCK(cs_main);
        ChainActive()[height]->nDataPos = original;
    }
};
// Fixture setup only, before any observer starts: copy the actual accepted
// native coin database into a file-backed LevelDB. No synthetic prevouts or
// production migration path. Destroy the borrowed cursor before owner swap.
bool FileBackedFixtureChainstate() {
    LOCK2(cs_main, g_mempool.cs);
    if (!pcoinsTip->Flush()) return false;
    CCoinsMap coins;
    {
        std::unique_ptr<CCoinsViewCursor> cursor(pcoinsdbview->Cursor());
        for (; cursor->Valid(); cursor->Next()) {
            COutPoint point;
            Coin coin;
            if (!cursor->GetKey(point) || !cursor->GetValue(coin) ||
                coin.IsSpent()) return false;
            auto inserted = coins.emplace(point, CCoinsCacheEntry(std::move(coin)));
            if (!inserted.second) return false;
            inserted.first->second.flags = CCoinsCacheEntry::DIRTY;
        }
    }
    auto database = std::make_unique<CCoinsViewDB>(1 << 20, false);
    if (!database->BatchWrite(coins, ChainActive().Tip()->GetBlockHash()))
        return false;
    pcoinsTip.reset();
    pcoinsdbview = std::move(database);
    pcoinsTip = std::make_unique<CCoinsViewCache>(pcoinsdbview.get());
    return fs::is_directory(GetDataDir() / "chainstate");
}
struct HttpReply {
    int status{0};
    std::vector<uint8_t> body;
    bool protobuf{false}, no_store{false};
};
HttpReply Http(uint16_t port, const std::string &path,
               const std::string &method = "GET") {
    boost::asio::io_context io;
    boost::asio::ip::tcp::socket socket(io);
    socket.connect({boost::asio::ip::address_v4::loopback(), port});
    const auto request = method + " " + path +
                         " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
                         "Content-Length: 0\r\n\r\n";
    boost::asio::write(socket, boost::asio::buffer(request));
    socket.non_blocking(true);
    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(3);
    std::string response;
    for (;;) {
        char bytes[4096];
        boost::system::error_code error;
        const auto n = socket.read_some(boost::asio::buffer(bytes), error);
        response.append(bytes, n);
        if (response.size() > 8 * 1024 * 1024)
            throw std::runtime_error("HTTP fixture response too large");
        if (error == boost::asio::error::eof ||
            error == boost::asio::error::connection_reset)
            break;
        if (error && error != boost::asio::error::would_block &&
            error != boost::asio::error::try_again)
            throw std::runtime_error("HTTP fixture read failed");
        if (std::chrono::steady_clock::now() > deadline)
            throw std::runtime_error("HTTP fixture read timeout");
        if (!n)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (response.empty())
        return {}; // Runtime cancellation before publication, never fake 200.
    const auto split = response.find("\r\n\r\n");
    if (split == std::string::npos)
        throw std::runtime_error("Incomplete HTTP fixture response");
    HttpReply reply;
    std::string version;
    std::istringstream status(response.substr(0, response.find("\r\n")));
    status >> version >> reply.status;
    if (version != "HTTP/1.1" || reply.status < 100 || reply.status > 599)
        throw std::runtime_error("Invalid HTTP fixture response");
    const auto headers = response.substr(0, split);
    reply.protobuf = headers.find("content-type: application/x-protobuf") !=
                     std::string::npos;
    reply.no_store =
        headers.find("cache-control: no-store") != std::string::npos;
    reply.body.assign(response.begin() + split + 4, response.end());
    return reply;
}
rust::Slice<const uint8_t> Body(const HttpReply &reply) {
    return {reply.body.data(), reply.body.size()};
}
template <typename Value> std::vector<uint8_t> Wire(const Value &value) {
    CDataStream stream(SER_NETWORK, PROTOCOL_VERSION);
    stream << value;
    return {stream.begin(), stream.end()};
}
std::vector<uint8_t> Ids(const std::vector<CMutableTransaction> &txs) {
    std::vector<uint8_t> out;
    for (const auto &tx : txs) {
        const auto id = Bytes(CTransaction(tx).GetId());
        out.insert(out.end(), id.begin(), id.end());
    }
    return out;
}
std::vector<uint8_t>
Points(const std::vector<std::pair<TxId, uint32_t>> &points) {
    std::vector<uint8_t> out;
    for (const auto &[id, index] : points) {
        const auto bytes = Bytes(id);
        out.insert(out.end(), bytes.begin(), bytes.end());
        for (int shift = 0; shift < 32; shift += 8)
            out.push_back(index >> shift);
    }
    return out;
}
void CheckHttp(uint16_t port, const std::string &path, uint8_t kind,
               const std::vector<uint8_t> &ids = {},
               const std::vector<uint64_t> &values = {}, int status = 200) {
    const auto reply = Http(port, path);
    BOOST_REQUIRE_MESSAGE(reply.status == status,
                          path << " status=" << reply.status);
    BOOST_CHECK(reply.protobuf && reply.no_store);
    BOOST_CHECK_MESSAGE(ergon_archival::http_fixture_check(
                            kind, Body(reply), {ids.data(), ids.size()},
                            {values.data(), values.size()}),
                        path);
}
CScript AlpScript(uint8_t op, const std::array<uint8_t, 32> &id = {}) {
    const auto script = ergon_archival::fixture_alp_script(op, id);
    return CScript(script.data(), script.data() + script.size());
}
} // namespace

BOOST_FIXTURE_TEST_SUITE(chronik_upstream_archival_tests, TestChain100Setup)
BOOST_AUTO_TEST_CASE(startup_owned_graph_and_source_storage_fences) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    auto bridge = ergon_archival::make_startup_bridge(access);
    const auto &tip = bridge->get_chain_tip();
    const auto old_hash = tip.GetBlockHash();
    const auto old_generation = bridge->chain_generation();
    {
        LOCK(cs_main);
        for (int h = 0; h <= tip.nHeight; ++h) {
            const auto &copy = chronik_bridge::get_block_ancestor(tip, h);
            BOOST_CHECK(&copy != ChainActive()[h]);
            BOOST_CHECK(copy.GetBlockHash() ==
                        ChainActive()[h]->GetBlockHash());
            BOOST_CHECK(copy.phashBlock != ChainActive()[h]->phashBlock);
            BOOST_CHECK(!copy.pskip);
            if (h)
                BOOST_CHECK(copy.pprev != ChainActive()[h]->pprev);
        }
    }
    // Real native progress on another joined thread, while startup owns all
    // ancestor/hash addresses. Upstream reads still see the original graph.
    std::exception_ptr failure;
    std::thread native([&] {
        try {
            CreateAndProcessBlock({}, CScript() << OP_2);
        } catch (...) {
            failure = std::current_exception();
        }
    });
    native.join();
    if (failure)
        std::rethrow_exception(failure);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    BOOST_CHECK_EQUAL(bridge->get_chain_tip().nHeight, 100);
    BOOST_CHECK(bridge->get_chain_tip().GetBlockHash() == old_hash);
    BOOST_CHECK_EQUAL(bridge->chain_generation(), old_generation);
    BOOST_CHECK(bridge->load_block(tip)->GetHash() == old_hash);
    BOOST_CHECK_EQUAL(bridge->load_block_undo(tip)->vtxundo.size(), 0);
    BOOST_CHECK_EXCEPTION(bridge->finish_startup_snapshot(), std::exception,
                          [](const std::exception &e) {
                              return std::string(e.what()).find(
                                         "source changed") != std::string::npos;
                          });
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_CHECK(ChainActive().Tip()->GetBlockHash() == old_hash);
    // Same hash and height do not erase the native generation contradiction.
    BOOST_CHECK_THROW(bridge->finish_startup_snapshot(), std::exception);
    auto storage = ergon_archival::make_startup_bridge(access);
    {
        DataPositionRestore restore(100);
        {
            LOCK(cs_main);
            ++ChainActive()[100]->nDataPos;
        }
        BOOST_CHECK_EXCEPTION(storage->finish_startup_snapshot(),
                              std::exception, [](const std::exception &e) {
                                  return std::string(e.what()).find(
                                             "storage changed") !=
                                         std::string::npos;
                              });
    }
    storage->finish_startup_snapshot();
    BOOST_CHECK_THROW(storage->finish_startup_snapshot(), std::exception);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!ShutdownRequested());
}
BOOST_AUTO_TEST_CASE(startup_drift_quarantines_database_without_stopping_node) {
    for (int kind = 0; kind != 3; ++kind) {
        chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
        const auto root =
            Root("archival-startup-drift-" + std::to_string(kind));
        const auto shutdown = ShutdownRequested();
        const auto hash = ChainActive().Tip()->GetBlockHash();
        ergon_archival::StartupHook hook([&] {
            AssertLockNotHeld(cs_main);
            AssertLockNotHeld(g_mempool.cs);
            if (kind == 2) {
                g_mempool.AddTransactionsUpdated(1);
            } else {
                CreateAndProcessBlock({}, CScript() << OP_3);
                if (kind == 1) {
                    CValidationState state;
                    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state,
                                                  ChainActive().Tip()));
                    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
                    BOOST_CHECK(ChainActive().Tip()->GetBlockHash() == hash);
                }
            }
        });
        BOOST_CHECK_EXCEPTION(
            ergon_archival::open_archival_with_hook(root, access, hook),
            std::exception, [](const std::exception &e) {
                return std::string(e.what()).find("source changed") !=
                       std::string::npos;
            });
        BOOST_CHECK(access.index_failed.load());
        BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
        BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
        BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
        chronik_bridge::ErgonNodeAccess retry(GetConfig(), g_mempool);
        BOOST_CHECK_THROW(ergon_archival::open_archival(root, retry),
                          std::exception);
        BOOST_CHECK(!retry.index_failed.load());
        CreateAndProcessBlock({}, CScript() << OP_4);
        auto fresh = ergon_archival::open_archival(
            Root("archival-startup-recovery-" + std::to_string(kind)), retry);
        BOOST_CHECK_EQUAL(fresh->snapshot().height, ChainActive().Height());
        BOOST_CHECK(!retry.index_failed.load());
    }
}
BOOST_AUTO_TEST_CASE(startup_nonempty_mempool_parent_child_and_reopen) {
    struct Policy {
        bool standard{fRequireStandard};
        int64_t clock{GetMockTime()};
        Policy() { fRequireStandard = false; }
        ~Policy() {
            fRequireStandard = standard;
            SetMockTime(clock);
        }
    } policy;
    const auto clock = GetTime() + 1000;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction parent;
    parent.vin.emplace_back(COutPoint(prev.GetId(), 0));
    parent.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(parent), 0,
                      SigHashType().withForkId(), Amount::zero()),
        signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    parent.vin[0].scriptSig << signature;
    CMutableTransaction child;
    child.vin.emplace_back(COutPoint(CTransaction(parent).GetId(), 0));
    child.vout.emplace_back(Amount::zero(), CScript() << OP_2);
    child.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 9));
    auto accept = [&](const auto &tx, int64_t time) {
        SetMockTime(time);
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    };
    accept(parent, clock);
    accept(child, clock + 1);
    BOOST_REQUIRE_EQUAL(g_mempool.size(), 2);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-nonempty-startup");
    {
        auto probe = ergon_archival::open_archival(root, access);
        const auto p = probe->tx_view(Bytes(CTransaction(parent).GetId()), 0);
        const auto c = probe->tx_view(Bytes(CTransaction(child).GetId()), 0);
        BOOST_CHECK_EQUAL(p.height, -1);
        BOOST_CHECK_EQUAL(c.height, -1);
        BOOST_CHECK_EQUAL(p.time_first_seen, clock);
        BOOST_CHECK_EQUAL(c.time_first_seen, clock + 1);
        BOOST_CHECK_EQUAL(p.mempool_input_height, 1);
        BOOST_CHECK(p.mempool_input_coinbase);
        BOOST_CHECK_EQUAL(c.mempool_input_height, -1);
        BOOST_CHECK(!c.mempool_input_coinbase);
        BOOST_CHECK(p.output_spent);
        BOOST_CHECK(p.spender == Bytes(CTransaction(child).GetId()));
        BOOST_CHECK_EQUAL(p.input_value, 0);
        BOOST_CHECK_EQUAL(probe->pool_capture_inventory(access).size(), 2);
        ergon_archival::CaptureStats stats;
        BOOST_REQUIRE(
            ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
        BOOST_CHECK_EQUAL(stats.prevout_reads, 0);
        BOOST_CHECK_EQUAL(stats.pool_reused, 2);
        BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 0);
        const auto port = probe->start_http_fixture();
        const auto reply =
            Http(port, "/tx/" + CTransaction(child).GetId().ToString());
        BOOST_CHECK_EQUAL(reply.status, 200);
        BOOST_CHECK(reply.protobuf);
        probe->stop_http_fixture();
    }
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    g_mempool.removeRecursive(CTransaction(child),
                              MemPoolRemovalReason::EXPIRY);
    {
        // Actual callback-owner startup, not only the low-level Rust box.
        ergon_archival::ArchivalWorker worker(root, access);
        BOOST_CHECK(worker.Status().ready);
        BOOST_CHECK(!worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0)
                         .output_spent);
        BOOST_CHECK_THROW(worker.ReadTx(Bytes(CTransaction(child).GetId()), 0),
                          std::exception);
        BOOST_CHECK_EQUAL(worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0)
                              .time_first_seen,
                          clock);
        const auto port = worker.StartHttpFixture();
        BOOST_CHECK_EQUAL(
            Http(port, "/tx/" + CTransaction(parent).GetId().ToString()).status,
            200);
        worker.Stop();
        BOOST_CHECK(worker.Status().joined);
        BOOST_CHECK(worker.Status().admission_closed);
    }
    accept(child, clock + 2);
    CreateAndProcessBlock({parent, child}, CScript() << OP_3);
    BOOST_REQUIRE_EQUAL(g_mempool.size(), 0);
    {
        auto probe = ergon_archival::open_archival(root, access);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).height, 101);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(child).GetId()), 0).height, 101);
        BOOST_CHECK_EQUAL(probe->pool_capture_inventory(access).size(), 0);
    }
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!ShutdownRequested());
}
BOOST_AUTO_TEST_CASE(actual_http_complete_read_profile_and_alp_lifecycle) {
    struct MempoolSignals {
        MempoolSignals() {
            GetMainSignals().RegisterWithMempoolSignals(g_mempool);
        }
        ~MempoolSignals() {
            GetMainSignals().UnregisterWithMempoolSignals(g_mempool);
            SyncWithValidationInterfaceQueue();
        }
    } signals;
    struct Policy {
        bool original{fRequireStandard};
        int64_t original_time{GetMockTime()};
        Policy() { fRequireStandard = false; }
        ~Policy() {
            fRequireStandard = original;
            SetMockTime(original_time);
        }
    } policy;
    const auto arrival_clock = GetTime() + 1000;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction genesis;
    genesis.vin.emplace_back(COutPoint(prev.GetId(), 0));
    genesis.vout.emplace_back(Amount::zero(), AlpScript(0));
    genesis.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    genesis.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto hash =
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(genesis), 0,
                      SigHashType().withForkId(), Amount::zero());
    std::vector<uint8_t> sig;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(hash, sig));
    sig.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    genesis.vin[0].scriptSig << sig;
    const auto gid = CTransaction(genesis).GetId();
    const auto genesis_bytes = Bytes(gid);
    CMutableTransaction mint;
    mint.vin.emplace_back(COutPoint(gid, 2));
    mint.vout.emplace_back(Amount::zero(), AlpScript(1, Bytes(gid)));
    mint.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    mint.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto mid = CTransaction(mint).GetId();
    CMutableTransaction send;
    send.vin.emplace_back(COutPoint(gid, 1));
    send.vin.emplace_back(COutPoint(mid, 1));
    send.vout.emplace_back(Amount::zero(), AlpScript(2, Bytes(gid)));
    send.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    send.vout.emplace_back(Amount::zero(), CScript() << OP_2);
    const auto sid = CTransaction(send).GetId();
    CMutableTransaction burn;
    burn.vin.emplace_back(COutPoint(sid, 2));
    burn.vout.emplace_back(Amount::zero(), AlpScript(3, Bytes(gid)));
    burn.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto bid = CTransaction(burn).GetId();
    const std::vector<CMutableTransaction> txs{genesis, mint, send, burn};
    const std::array<int32_t, 4> kinds{2, 4, 3, 5};
    const std::array<uint64_t, 4> atoms{1000, 500, 1500, 0};
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-http-alp-lifecycle");
    auto worker =
        std::make_unique<ergon_archival::ArchivalWorker>(root, access);
    auto port = worker->StartHttpFixture();
    for (size_t i = 0; i < txs.size(); ++i) {
        // Deliberately distinct real native entry times. Do not derive the
        // expected timestamps from the indexer under test or wall-clock races.
        SetMockTime(arrival_clock + i);
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(txs[i]),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    }
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker->WaitThrough(worker->Status().accepted, 5000));
    std::array<int64_t, 4> times;
    for (size_t i = 0; i < txs.size(); ++i) {
        times[i] = g_mempool.info(CTransaction(txs[i]).GetId()).nTime;
        BOOST_REQUIRE_GT(times[i], 0);
        BOOST_REQUIRE_EQUAL(times[i], arrival_clock + i);
    }
    const auto token_points = Points({{sid, 1}, {mid, 2}});
    const auto script_points = Points({{sid, 1}, {mid, 2}, {bid, 1}});
    const auto token_path = "/token-id/" + gid.ToString();
    const std::string script_path = "/script/other/51";
    const std::string lokad_path = "/lokad-id/534c5032";
    auto validate = [&](bool confirmed, const CBlock &tip,
                        const std::array<int64_t, 4> &first_seen) {
        const auto indexed_height = confirmed ? int32_t{101} : int32_t{-1};
        const auto wire_height = confirmed ? uint64_t{101} : UINT64_MAX;
        const auto tip_height = confirmed ? 101 : 100;
        const auto tip_hash = Bytes(tip.GetHash());
        const auto info = Http(port, "/blockchain-info");
        BOOST_REQUIRE_EQUAL(info.status, 200);
        BOOST_CHECK(info.protobuf && info.no_store);
        BOOST_CHECK(ergon_archival::http_chain_matches(Body(info), tip_hash,
                                                       tip_height));
        const auto version = FormatFullVersion();
        CheckHttp(port, "/chronik-info", 0, {version.begin(), version.end()});
        const std::string height = std::to_string(tip_height);
        CheckHttp(
            port, "/block/" + height, 1, {tip_hash.begin(), tip_hash.end()},
            {uint64_t(tip_height), confirmed ? uint64_t{5} : uint64_t{1}});
        CheckHttp(
            port, "/block/" + tip.GetHash().ToString(), 1,
            {tip_hash.begin(), tip_hash.end()},
            {uint64_t(tip_height), confirmed ? uint64_t{5} : uint64_t{1}});
        const auto header = Wire(tip.GetBlockHeader());
        CheckHttp(port, "/block-header/" + height, 3, header);
        CheckHttp(port, "/blocks/" + height + "/" + height, 2,
                  {tip_hash.begin(), tip_hash.end()}, {uint64_t(tip_height)});
        CheckHttp(port, "/block-headers/" + height + "/" + height, 4, header);
        std::vector<CMutableTransaction> ordered;
        for (const auto &tx : tip.vtx)
            ordered.emplace_back(*tx);
        CheckHttp(port, "/block-txs/" + height, 5, Ids(ordered),
                  {uint64_t(ordered.size()), 1, 1});
        CheckHttp(port, "/block-txs/" + height + "?page=0&page_size=1", 5,
                  Ids({ordered[0]}),
                  {uint64_t(ordered.size()), uint64_t(ordered.size()), 1});
        CheckHttp(port, "/unconfirmed-txs", 5,
                  confirmed ? std::vector<uint8_t>{} : Ids(txs),
                  {confirmed ? uint64_t{0} : uint64_t{4},
                   confirmed ? uint64_t{0} : uint64_t{1}, 0});
        for (size_t i = 0; i < txs.size(); ++i) {
            const auto id = CTransaction(txs[i]).GetId();
            const auto reply = Http(port, "/tx/" + id.ToString());
            BOOST_REQUIRE_EQUAL(reply.status, 200);
            BOOST_CHECK(reply.protobuf && reply.no_store);
            BOOST_CHECK_MESSAGE(ergon_archival::http_alp_tx_matches(
                                    Body(reply), Bytes(id), Bytes(gid),
                                    kinds[i], atoms[i], i == 3 ? 100 : 0, false,
                                    indexed_height, first_seen[i]),
                                "ALP operation=" << kinds[i]);
            CheckHttp(port, "/raw-tx/" + id.ToString(), 6,
                      Wire(CTransaction(txs[i])));
        }
        CheckHttp(port, "/token/" + gid.ToString(), 7,
                  {genesis_bytes.begin(), genesis_bytes.end()},
                  {wire_height, uint64_t(first_seen[0])});
        BOOST_CHECK(ergon_archival::http_spent_matches(
            Body(Http(port, "/tx/" + gid.ToString())), 1, Bytes(sid), 0));
        BOOST_CHECK(ergon_archival::http_spent_matches(
            Body(Http(port, "/tx/" + gid.ToString())), 2, Bytes(mid), 0));
        BOOST_CHECK(ergon_archival::http_spent_matches(
            Body(Http(port, "/tx/" + mid.ToString())), 1, Bytes(sid), 1));
        BOOST_CHECK(ergon_archival::http_spent_matches(
            Body(Http(port, "/tx/" + sid.ToString())), 2, Bytes(bid), 0));
        std::vector<CMutableTransaction> history;
        if (confirmed)
            history.assign(ordered.begin() + 1, ordered.end());
        const auto history_ids = Ids(history);
        std::reverse(history.begin(), history.end());
        // Upstream /history is reverse block order, stably sorted by descending
        // first-seen time within the block. /confirmed-txs remains block order.
        auto native_time = [&](const CMutableTransaction &tx) {
            for (size_t i = 0; i < txs.size(); ++i)
                if (CTransaction(tx).GetId() == CTransaction(txs[i]).GetId())
                    return first_seen[i];
            throw std::runtime_error("Unexpected native history transaction");
        };
        std::stable_sort(history.begin(), history.end(),
                         [&](const auto &a, const auto &b) {
                             return native_time(a) > native_time(b);
                         });
        for (const auto &prefix : {script_path, token_path, lokad_path}) {
            CheckHttp(port, prefix + "/confirmed-txs", 5, history_ids,
                      {confirmed ? uint64_t{4} : uint64_t{0},
                       confirmed ? uint64_t{1} : uint64_t{0}, 1});
            CheckHttp(port, prefix + "/history", 5,
                      confirmed ? Ids(history) : Ids(txs),
                      {4, 1, confirmed ? uint64_t{1} : uint64_t{0}});
            CheckHttp(port, prefix + "/unconfirmed-txs", 5,
                      confirmed ? std::vector<uint8_t>{} : Ids(txs),
                      {confirmed ? uint64_t{0} : uint64_t{4},
                       confirmed ? uint64_t{0} : uint64_t{1}, 0});
        }
        if (confirmed) {
            CheckHttp(port, token_path + "/confirmed-txs?page=1&page_size=1", 5,
                      Ids({ordered[2]}), {4, 4, 1});
        }
        CheckHttp(port, script_path + "/utxos", 8, script_points,
                  {3, 1400, 1, wire_height});
        CheckHttp(port, token_path + "/utxos", 9, token_points,
                  {2, 1400, 1, wire_height});
        const auto count = worker->RequestAdmission()->dispatched();
        for (const auto &bad : {"/lokad-id/534c5032/utxos", "/ws", "/pause",
                                "/resume", "/plugin/not-loaded/00/history"})
            BOOST_CHECK_EQUAL(Http(port, bad).status, 501);
        for (const auto &bad : {"/block-header/100?checkpoint_height=100",
                                "/tx/nothex?wait_for_finalization=1",
                                "/token-id/00/history?page=0&page=1"})
            BOOST_CHECK_EQUAL(Http(port, bad).status, 400);
        BOOST_CHECK_EQUAL(worker->RequestAdmission()->dispatched(), count);
        const std::string bad_id(64, '0');
        const std::string error_prefix = "404:";
        CheckHttp(port, "/tx/" + bad_id, 10,
                  {error_prefix.begin(), error_prefix.end()}, {}, 404);
        CheckHttp(port, "/block/2000", 10,
                  {error_prefix.begin(), error_prefix.end()}, {}, 404);
    };
    // The accepted native chain is authoritative; mempool metadata is indexed
    // only by the actual upstream ALP/token writers.
    CBlock previous;
    BOOST_REQUIRE(
        ReadBlockFromDisk(previous, ChainActive().Tip(),
                          GetConfig().GetChainParams().GetConsensus()));
    validate(false, previous, times);
    const auto block = CreateAndProcessBlock(txs, prev.vout[0].scriptPubKey);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker->WaitThrough(worker->Status().accepted, 5000));
    BOOST_CHECK_EQUAL(g_mempool.size(), 0);
    validate(true, block, times);
    SetMockTime(arrival_clock + 100);
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker->WaitThrough(worker->Status().accepted, 5000));
    BOOST_REQUIRE_EQUAL(g_mempool.size(), 4);
    std::array<int64_t, 4> restored_times;
    for (size_t i = 0; i < txs.size(); ++i) {
        restored_times[i] = g_mempool.info(CTransaction(txs[i]).GetId()).nTime;
        BOOST_REQUIRE_EQUAL(restored_times[i], arrival_clock + 100);
        BOOST_REQUIRE_GT(restored_times[i], times[i]);
    }
    validate(false, previous, restored_times);
    const auto alternate = CreateAndProcessBlock(txs, CScript() << OP_3);
    BOOST_REQUIRE(alternate.GetHash() != block.GetHash());
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker->WaitThrough(worker->Status().accepted, 5000));
    validate(true, alternate, restored_times);
    worker->Stop();
    worker = std::make_unique<ergon_archival::ArchivalWorker>(root, access);
    port = worker->StartHttpFixture();
    validate(true, alternate, restored_times);
    worker->Stop();
    // Fresh rebuild has no invented mempool arrival times. Compare the same
    // full projection with explicit zero/unknown timestamps, not fake equality.
    worker = std::make_unique<ergon_archival::ArchivalWorker>(
        Root("archival-http-alp-rebuild"), access);
    port = worker->StartHttpFixture();
    validate(true, alternate, {});
    worker->Stop();
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(
    actual_alp_invalid_mint_and_malformed_metadata_do_not_change_consensus) {
    struct Signals {
        Signals() { GetMainSignals().RegisterWithMempoolSignals(g_mempool); }
        ~Signals() {
            GetMainSignals().UnregisterWithMempoolSignals(g_mempool);
            SyncWithValidationInterfaceQueue();
        }
    } signals;
    struct Policy {
        bool original{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = original; }
    } policy;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction genesis;
    genesis.vin.emplace_back(COutPoint(prev.GetId(), 0));
    genesis.vout.emplace_back(Amount::zero(), AlpScript(0));
    genesis.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    genesis.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto hash =
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(genesis), 0,
                      SigHashType().withForkId(), Amount::zero());
    std::vector<uint8_t> sig;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(hash, sig));
    sig.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    genesis.vin[0].scriptSig << sig;
    CreateAndProcessBlock({genesis}, prev.vout[0].scriptPubKey);
    const auto gid = CTransaction(genesis).GetId();
    CMutableTransaction bad_mint;
    // Consume atoms, NOT the still-unspent mint baton. The native node has no
    // ALP validity authority; the upstream indexer must report this as invalid.
    bad_mint.vin.emplace_back(COutPoint(gid, 1));
    bad_mint.vout.emplace_back(Amount::zero(), AlpScript(1, Bytes(gid)));
    bad_mint.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    bad_mint.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto mid = CTransaction(bad_mint).GetId();
    CMutableTransaction malformed;
    malformed.vin.emplace_back(COutPoint(mid, 1));
    malformed.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << OP_RESERVED
                                  << std::vector<uint8_t>{'S', 'L', 'P', '2'}
                                  << std::vector<uint8_t>(64, 0));
    malformed.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-http-alp-invalid");
    ergon_archival::ArchivalWorker worker(root, access);
    const auto port = worker.StartHttpFixture();
    for (const auto &tx : {bad_mint, malformed}) {
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    }
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    const auto first_seen = g_mempool.info(mid).nTime;
    BOOST_REQUIRE_GT(first_seen, 0);
    auto check = [&](int height) {
        const auto invalid = Http(port, "/tx/" + mid.ToString());
        BOOST_REQUIRE_EQUAL(invalid.status, 200);
        BOOST_CHECK(ergon_archival::http_alp_tx_matches(
            Body(invalid), Bytes(mid), Bytes(gid), 4, 0, 1000, true, height,
            first_seen));
        const auto parsed =
            Http(port, "/tx/" + CTransaction(malformed).GetId().ToString());
        BOOST_REQUIRE_EQUAL(parsed.status, 200);
        BOOST_CHECK(ergon_archival::http_alp_parse_failure_matches(
            Body(parsed), Bytes(CTransaction(malformed).GetId())));
        CheckHttp(port, "/token-id/" + gid.ToString() + "/utxos", 9,
                  Points({{gid, 2}}), {1, 0, 1, 101});
        BOOST_CHECK_EQUAL(Http(port, "/blockchain-info").status, 200);
        BOOST_CHECK(!access.index_failed.load());
    };
    BOOST_CHECK_EQUAL(g_mempool.size(), 2);
    check(-1);
    const auto accepted =
        CreateAndProcessBlock({bad_mint, malformed}, CScript() << OP_3);
    BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash() == accepted.GetHash());
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    check(102);
    worker.Stop();
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(native_source_generation_counter_laws) {
    SourceGeneration epoch;
    BOOST_CHECK_EQUAL(epoch.Value(), 1);
    epoch.Advance(0);
    BOOST_CHECK_EQUAL(epoch.Value(), 1);
    epoch.Advance(UINT64_MAX - 2);
    BOOST_CHECK_EQUAL(epoch.Value(), UINT64_MAX - 1);
    epoch.Advance();
    BOOST_CHECK_EQUAL(epoch.Value(), UINT64_MAX);
    epoch.Advance();
    BOOST_CHECK_EQUAL(epoch.Value(), 0);
    epoch.Advance(0);
    epoch.Advance(1);
    BOOST_CHECK_EQUAL(epoch.Value(), 0);
    CTxMemPool pool;
    const auto legacy = pool.GetTransactionsUpdated();
    const auto before = pool.GetSourceGeneration();
    pool.AddTransactionsUpdated(UINT32_MAX);
    pool.AddTransactionsUpdated(1);
    BOOST_CHECK_EQUAL(pool.GetTransactionsUpdated(), legacy);
    BOOST_CHECK_EQUAL(pool.GetSourceGeneration(), before + (uint64_t{1} << 32));
    pool.clear();
    BOOST_CHECK_EQUAL(pool.GetSourceGeneration(),
                      before + (uint64_t{1} << 32) + 1);
    CChain chain;
    CBlockIndex first, second;
    first.nHeight = 0;
    second.nHeight = 1;
    second.pprev = &first;
    chain.SetTip(&first);
    const auto branch = chain.GetSourceGeneration();
    chain.SetTip(&second);
    chain.SetTip(&first);
    BOOST_CHECK(chain.Tip() == &first);
    BOOST_CHECK_EQUAL(chain.GetSourceGeneration(), branch + 2);
    chain.SetTip(&first);
    BOOST_CHECK_EQUAL(chain.GetSourceGeneration(), branch + 2);
    chain.SetTip(nullptr);
    BOOST_CHECK_EQUAL(chain.GetSourceGeneration(), branch + 3);
}
BOOST_AUTO_TEST_CASE(native_source_generation_delayed_callback_aba) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-native-generation-aba");
    ergon_archival::ArchivalWorker worker(root, access);
    const auto port = worker.StartHttpFixture();
    auto gate = worker.RequestAdmission();
    auto response_barrier = worker.ResponseBarrier();
    BOOST_REQUIRE_EQUAL(Http(port, "/blockchain-info").status, 200);
    BOOST_REQUIRE(response_barrier->arm());
    auto in_flight = std::async(std::launch::async,
                                [&] { return Http(port, "/blockchain-info"); });
    BOOST_REQUIRE(response_barrier->wait(5000));
    CallbackLatch delayed;
    CallFunctionInValidationInterfaceQueue([&] { delayed.Block(); });
    BOOST_REQUIRE(delayed.Wait());
    CBlockIndex *old_tip;
    uint64_t old_chain, old_pool;
    unsigned int legacy;
    {
        LOCK2(cs_main, g_mempool.cs);
        old_tip = ChainActive().Tip();
        old_chain = ChainActive().GetSourceGeneration();
        old_pool = g_mempool.GetSourceGeneration();
        legacy = g_mempool.GetTransactionsUpdated();
    }
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, old_tip));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    {
        LOCK(cs_main);
        ResetBlockFailureFlags(old_tip);
    }
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    {
        LOCK2(cs_main, g_mempool.cs);
        BOOST_CHECK(ChainActive().Tip() == old_tip);
        // Restore the wrapping legacy counter without resetting the native
        // source epoch. Tip + old counter alone would falsely accept ABA.
        g_mempool.AddTransactionsUpdated(legacy -
                                         g_mempool.GetTransactionsUpdated());
        BOOST_CHECK_EQUAL(g_mempool.GetTransactionsUpdated(), legacy);
        BOOST_CHECK_GT(ChainActive().GetSourceGeneration(), old_chain);
        BOOST_CHECK_GT(g_mempool.GetSourceGeneration(), old_pool);
    }
    BOOST_CHECK_EQUAL(worker.Status().accepted, 0);
    response_barrier->release();
    BOOST_CHECK_EQUAL(in_flight.get().status, 503);
    const auto dispatched = gate->dispatched();
    BOOST_CHECK_EQUAL(Http(port, "/blockchain-info").status, 503);
    BOOST_CHECK_EQUAL(gate->dispatched(), dispatched);
    delayed.Release();
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    BOOST_CHECK_EQUAL(Http(port, "/blockchain-info").status, 200);
    worker.Stop();
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(actual_http_worker_admission_and_generation_rejection) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-http-admission");
    CallbackLatch capture;
    std::atomic<bool> block_capture{false};
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, [&] {
        if (block_capture.load())
            capture.Block();
    });
    const auto port = worker.StartHttpFixture();
    BOOST_CHECK_THROW(worker.StartHttpFixture(), std::exception);
    auto gate = worker.RequestAdmission();
    auto response_barrier = worker.ResponseBarrier();
    auto reply = Http(port, "/blockchain-info");
    BOOST_REQUIRE_EQUAL(reply.status, 200);
    BOOST_CHECK(reply.protobuf && reply.no_store);
    BOOST_CHECK(ergon_archival::http_chain_matches(
        Body(reply), Bytes(ChainActive().Tip()->GetBlockHash()), 100));
    const auto dispatches = gate->dispatched();
    for (const auto &path :
         {"/pause", "/resume", "/ws", "/plugin/example/00/utxos"}) {
        const auto refused = Http(port, path);
        BOOST_CHECK_EQUAL(refused.status, 501);
        BOOST_CHECK(ergon_archival::http_error_matches(
            Body(refused),
            "501: Capability not enabled for this Ergon observer profile"));
    }
    for (const auto &path : {"/broadcast-tx", "/broadcast-txs", "/validate-tx"})
        BOOST_CHECK_EQUAL(Http(port, path, "POST").status, 501);
    for (const auto &path :
         {"/blockchain-info?wait_for_finalization=1",
          "/block-txs/100?page=0&page=1", "/block-txs/100?page_size=201"})
        BOOST_CHECK_EQUAL(Http(port, path).status, 400);
    BOOST_CHECK_EQUAL(gate->dispatched(), dispatches);
    {
        // Request source checks must NOT wait behind a native lock owner.
        LOCK2(cs_main, g_mempool.cs);
        BOOST_CHECK_EQUAL(Http(port, "/blockchain-info").status, 503);
    }
    BOOST_CHECK_EQUAL(gate->dispatched(), dispatches);
    block_capture.store(true);
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(capture.Wait());
    reply = Http(port, "/blockchain-info");
    BOOST_CHECK_EQUAL(reply.status, 503);
    BOOST_CHECK(ergon_archival::http_error_matches(
        Body(reply), "503: Ergon index view unavailable"));
    BOOST_CHECK_EQUAL(gate->dispatched(), dispatches);
    block_capture.store(false);
    capture.Release();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    reply = Http(port, "/blockchain-info");
    BOOST_REQUIRE_EQUAL(reply.status, 200);
    BOOST_CHECK(ergon_archival::http_chain_matches(
        Body(reply), Bytes(ChainActive().Tip()->GetBlockHash()), 101));
    // Actual upstream body is already complete, but not yet published.
    BOOST_REQUIRE(response_barrier->arm());
    auto stale = std::async(std::launch::async,
                            [&] { return Http(port, "/blockchain-info"); });
    BOOST_REQUIRE(response_barrier->wait(5000));
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    response_barrier->release();
    const auto discarded = stale.get();
    BOOST_CHECK_EQUAL(discarded.status, 503);
    BOOST_CHECK(ergon_archival::http_error_matches(
        Body(discarded), "503: Ergon index generation changed during request"));
    // Same-height branch change is not hidden by a height-only check.
    BOOST_REQUIRE(response_barrier->arm());
    auto reorg_read = std::async(
        std::launch::async, [&] { return Http(port, "/blockchain-info"); });
    BOOST_REQUIRE(response_barrier->wait(5000));
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    CreateAndProcessBlock({}, CScript() << OP_3);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    response_barrier->release();
    BOOST_CHECK_EQUAL(reorg_read.get().status, 503);
    reply = Http(port, "/blockchain-info");
    BOOST_REQUIRE_EQUAL(reply.status, 200);
    BOOST_CHECK(ergon_archival::http_chain_matches(
        Body(reply), Bytes(ChainActive().Tip()->GetBlockHash()), 102));
    // Metadata-only invalidation with native values unchanged: the admission
    // generation, not just the native tip comparison, must reject this body.
    BOOST_REQUIRE(response_barrier->arm());
    auto generation_read = std::async(
        std::launch::async, [&] { return Http(port, "/blockchain-info"); });
    BOOST_REQUIRE(response_barrier->wait(5000));
    gate->invalidate();
    response_barrier->release();
    BOOST_CHECK_EQUAL(generation_read.get().status, 503);
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    reply = Http(port, "/blockchain-info");
    BOOST_REQUIRE_EQUAL(reply.status, 200);
    BOOST_CHECK(ergon_archival::http_chain_matches(
        Body(reply), Bytes(ChainActive().Tip()->GetBlockHash()), 103));
    const auto before_stop = worker.ReadSnapshot();
    BOOST_REQUIRE(response_barrier->arm());
    auto closing = std::async(std::launch::async,
                              [&] { return Http(port, "/blockchain-info"); });
    BOOST_REQUIRE(response_barrier->wait(5000));
    // Do NOT release the async latch. Positive runtime stop cancels it.
    worker.Stop();
    BOOST_CHECK_EQUAL(closing.get().status, 0);
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK_THROW(Http(port, "/blockchain-info"), std::exception);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!access.index_failed.load());
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK(reopened->snapshot().logical_digest ==
                before_stop.logical_digest);
}
BOOST_AUTO_TEST_CASE(silent_mempool_generation_recovers_without_callback) {
    struct Policy {
        bool standard{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = standard; }
    } policy;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction tx;
    tx.vin.emplace_back(COutPoint(prev.GetId(), 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(tx), 0,
                      SigHashType().withForkId(), Amount::zero()),
        signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    tx.vin[0].scriptSig << signature;
    {
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    }
    SyncWithValidationInterfaceQueue();
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-silent-generation");
    std::atomic<int> drift{0};
    const auto id = CTransaction(tx).GetId();
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, [&] {
        if (drift.load() > 0) {
            --drift;
            g_mempool.PrioritiseTransaction(id, FIXOSHI);
        }
    });
    const auto port = worker.StartHttpFixture();
    auto latch = worker.ResponseBarrier();
    BOOST_REQUIRE_EQUAL(Http(port, "/tx/" + id.ToString()).status, 200);
    BOOST_REQUIRE(latch->arm());
    auto stale = std::async(std::launch::async,
                            [&] { return Http(port, "/blockchain-info"); });
    const bool entered = latch->wait(5000);
    const auto old_revision = g_mempool.GetSourceGeneration();
    // Exhaust one four-attempt capture, then leave the source quiet. The
    // autonomous retry must recover too, without any synthetic callback.
    drift.store(4);
    g_mempool.PrioritiseTransaction(id, FIXOSHI);
    latch->release();
    const auto refused = stale.get();
    BOOST_REQUIRE(entered);
    BOOST_CHECK_EQUAL(refused.status, 503);
    BOOST_CHECK(g_mempool.GetSourceGeneration() != old_revision);
    // No native block or transaction notification is manufactured to recover.
    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(5);
    HttpReply recovered;
    do {
        recovered = Http(port, "/tx/" + id.ToString());
        if (recovered.status == 200)
            break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    } while (std::chrono::steady_clock::now() < deadline);
    BOOST_CHECK_EQUAL(recovered.status, 200);
    BOOST_CHECK_EQUAL(drift.load(), 0);
    BOOST_CHECK(recovered.protobuf && recovered.no_store);
    BOOST_CHECK_EQUAL(g_mempool.size(), 1);
    BOOST_CHECK_EQUAL(worker.ReadTx(Bytes(id), 0).height, -1);
    BOOST_CHECK_EQUAL(worker.Status().accepted, 0);
    BOOST_CHECK(worker.Status().ready);
    BOOST_CHECK(!worker.Status().failed);
    BOOST_CHECK(!access.index_failed.load());
    worker.Stop();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(control_handles_do_not_wait_behind_native_capture) {
    // The escape throws BEFORE the worker next takes a native lock. Thus the
    // old blocking implementation fails this regression without deadlocking.
    for (bool pool_lock : {false, true}) {
        chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
        const auto root = Root(pool_lock ? "archival-control-pool"
                                         : "archival-control-chain");
        CallbackLatch disk;
        std::atomic<bool> armed{false}, escape{false};
        ergon_archival::ArchivalWorker worker(
            root, access, 128, {}, {}, {}, [&] {
                if (armed.load()) {
                    disk.Block();
                    if (escape.load())
                        throw std::runtime_error("Regression deadlock escape");
                }
            });
        const auto port = worker.StartHttpFixture();
        auto original_gate = worker.RequestAdmission();
        auto original_latch = worker.ResponseBarrier();
        armed.store(true);
        CreateAndProcessBlock({}, CScript() << OP_1);
        SyncWithValidationInterfaceQueue();
        const bool at_disk = disk.Wait();
        std::unique_ptr<rust::Box<ergon_archival::RequestGate>> gate;
        std::unique_ptr<rust::Box<ergon_archival::ResponseLatch>> latch;
        std::promise<void> native_locked;
        auto locked = native_locked.get_future();
        auto controls = std::async(std::launch::async, [&] {
            auto retrieve = [&] {
                native_locked.set_value();
                try {
                    gate = std::make_unique<
                        rust::Box<ergon_archival::RequestGate>>(
                        worker.RequestAdmission());
                    latch = std::make_unique<
                        rust::Box<ergon_archival::ResponseLatch>>(
                        worker.ResponseBarrier());
                    return true;
                } catch (const std::exception &) {
                    return false;
                }
            };
            if (pool_lock) {
                LOCK(g_mempool.cs);
                return retrieve();
            }
            LOCK(cs_main);
            return retrieve();
        });
        locked.wait();
        const bool prompt = controls.wait_for(std::chrono::milliseconds(100)) ==
                            std::future_status::ready;
        if (!prompt)
            escape.store(true);
        // On the fixed path both native locks are free for actual validation
        // while the worker still owns its latched data mutex.
        if (prompt) {
            const auto height = ChainActive().Height();
            CreateAndProcessBlock({}, CScript() << OP_2);
            SyncWithValidationInterfaceQueue();
            BOOST_CHECK_EQUAL(ChainActive().Height(), height + 1);
        }
        armed.store(false);
        disk.Release();
        const bool retrieved = controls.get();
        BOOST_CHECK(at_disk);
        BOOST_CHECK(prompt);
        BOOST_CHECK(retrieved);
        if (prompt && retrieved) {
            BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
            BOOST_REQUIRE((*latch)->arm());
            auto response = std::async(std::launch::async, [&] {
                return Http(port, "/blockchain-info");
            });
            const bool shared = original_latch->wait(5000);
            original_latch->release();
            const auto reply = response.get();
            // The idle observer itself can briefly own a native fence. The
            // final nonblocking source check must then discard the buffered
            // body, not wait or invent a healthy response.
            BOOST_CHECK(reply.status == 200 ||
                        (reply.status == 503 &&
                         ergon_archival::http_error_matches(
                             Body(reply), "503: Ergon index generation changed "
                                          "during request")));
            BOOST_CHECK(shared);
            const auto deadline = std::chrono::steady_clock::now() +
                                  std::chrono::seconds(5);
            HttpReply healthy;
            do {
                healthy = Http(port, "/blockchain-info");
                if (healthy.status == 200)
                    break;
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            } while (std::chrono::steady_clock::now() < deadline);
            BOOST_CHECK_EQUAL(healthy.status, 200);
            BOOST_CHECK(ergon_archival::http_chain_matches(
                Body(healthy), Bytes(ChainActive().Tip()->GetBlockHash()),
                ChainActive().Height()));
            BOOST_CHECK_EQUAL((*gate)->dispatched(),
                              original_gate->dispatched());
            BOOST_CHECK(!access.index_failed.load());
        }
        worker.Stop();
        BOOST_CHECK(worker.Status().joined);
        BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    }
}
BOOST_AUTO_TEST_CASE(actual_http_fault_admission_does_not_stop_native_node) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-http-fault");
    CallbackLatch capture;
    ergon_archival::ArchivalWorker worker(root, access, 1, {}, {},
                                          [&] { capture.Block(); });
    const auto port = worker.StartHttpFixture();
    auto gate = worker.RequestAdmission();
    BOOST_REQUIRE_EQUAL(Http(port, "/blockchain-info").status, 200);
    const auto dispatches = gate->dispatched();
    const auto shutdown_before = ShutdownRequested();
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(capture.Wait());
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK(worker.Status().failed);
    BOOST_CHECK_EQUAL(Http(port, "/blockchain-info").status, 503);
    BOOST_CHECK_EQUAL(gate->dispatched(), dispatches);
    CreateAndProcessBlock({}, CScript() << OP_3);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 103);
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
    capture.Release();
    worker.Stop();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK_THROW(Http(port, "/blockchain-info"), std::exception);
}
BOOST_AUTO_TEST_CASE(captured_gap_retries_and_busy_source_stays_unavailable) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-captured-gap");
    auto probe = ergon_archival::open_archival(root, access);
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    int gaps = 0;
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access, [&] {
        if (++gaps == 1) {
            // This phase must own neither native lock: native validation can
            // advance before the DB application, invalidating publication.
            AssertLockNotHeld(cs_main);
            AssertLockNotHeld(g_mempool.cs);
            CreateAndProcessBlock({}, CScript() << OP_1);
            BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        }
    }));
    BOOST_CHECK_EQUAL(gaps, 2);
    BOOST_CHECK_EQUAL(probe->snapshot().height, 101);
    gaps = 0;
    BOOST_CHECK(!ergon_archival::reconcile_captured(*probe, access, [&] {
        ++gaps;
        CreateAndProcessBlock({}, CScript() << OP_2);
    }));
    BOOST_CHECK_EQUAL(gaps, 4);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 105);
    BOOST_CHECK_THROW(probe->snapshot(), std::exception);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    BOOST_CHECK_EQUAL(probe->snapshot().height, 105);
    BOOST_CHECK(!access.index_failed.load());
    // An unpublished captured branch must be a valid rewind origin on retry.
    const auto old = CreateAndProcessBlock({}, CScript() << OP_1);
    gaps = 0;
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access, [&] {
        if (++gaps == 1) {
            CValidationState state;
            BOOST_REQUIRE(
                InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
            BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
            CreateAndProcessBlock({}, CScript() << OP_3);
            BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        }
    }));
    BOOST_CHECK_EQUAL(gaps, 2);
    const auto rewritten = probe->snapshot();
    BOOST_CHECK_EQUAL(rewritten.height, 106);
    BOOST_CHECK(rewritten.hash == Bytes(ChainActive().Tip()->GetBlockHash()));
    BOOST_CHECK(rewritten.hash != Bytes(old.GetHash()));
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(bounded_capture_window_and_deep_reorg_fail_closed) {
    while (ChainActive().Height() < 400)
        CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-bounded-window");
    auto probe = ergon_archival::open_archival(root, access);
    ergon_archival::CaptureStats stats;
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
    BOOST_CHECK_EQUAL(stats.metadata_copied, 0);
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 288);
    auto window = probe->capture_inventory(access);
    BOOST_REQUIRE_EQUAL(window.size(), 288);
    BOOST_CHECK_EQUAL(window[0].height, 113);
    CreateAndProcessBlock({}, CScript() << OP_2);
    CreateAndProcessBlock({}, CScript() << OP_3);
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
    BOOST_CHECK_EQUAL(stats.metadata_copied, 2);
    BOOST_CHECK_EQUAL(stats.block_read_calls, 2);
    BOOST_CHECK(stats.metadata_checked <= 3 * 290);
    window = probe->capture_inventory(access);
    BOOST_REQUIRE_EQUAL(window.size(), 288);
    BOOST_CHECK_EQUAL(window[0].height, 115);
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
    BOOST_CHECK_EQUAL(stats.metadata_copied, 0);
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    window = probe->capture_inventory(access);
    BOOST_REQUIRE_EQUAL(window.size(), 287);
    BOOST_CHECK_EQUAL(window[0].height, 115);
    const auto shutdown = ShutdownRequested();
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive()[115]));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_CHECK_EXCEPTION(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats),
        std::exception, [](const std::exception &e) {
            return std::string(e.what()).find("below retained anchor") !=
                   std::string::npos;
        });
    BOOST_CHECK_EQUAL(stats.metadata_copied, 0);
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK_THROW(probe->snapshot(), std::exception);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 114);
    CreateAndProcessBlock({}, CScript() << OP_4);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 115);
    // Explicit fresh owned index after failure, never silent adoption/wipe.
    chronik_bridge::ErgonNodeAccess rebuilt_access(GetConfig(), g_mempool);
    auto fresh = ergon_archival::open_archival(
        Root("archival-after-deep-rebuild"), rebuilt_access);
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*fresh, rebuilt_access));
    BOOST_CHECK_EQUAL(fresh->snapshot().height, 115);
}
BOOST_AUTO_TEST_CASE(mempool_capture_and_writer_deltas_preserve_dependencies) {
    struct Policy {
        bool standard{fRequireStandard};
        int64_t time{GetMockTime()};
        Policy() { fRequireStandard = false; }
        ~Policy() {
            fRequireStandard = standard;
            SetMockTime(time);
        }
    } policy;
    const auto clock = GetTime() + 1000;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction parent;
    parent.vin.emplace_back(COutPoint(prev.GetId(), 0));
    parent.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(parent), 0,
                      SigHashType().withForkId(), Amount::zero()),
        signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    parent.vin[0].scriptSig << signature;
    CMutableTransaction child;
    child.vin.emplace_back(COutPoint(CTransaction(parent).GetId(), 0));
    child.vout.emplace_back(Amount::zero(), CScript() << OP_2);
    // Meet the inherited native minimum wire size; do not relax policy.
    child.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 7));
    auto accept = [&](const auto &tx, int64_t time) {
        SetMockTime(time);
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    };
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    auto probe =
        ergon_archival::open_archival(Root("archival-pool-deltas"), access);
    ergon_archival::CaptureStats stats;
    auto reconcile = [&] {
        BOOST_REQUIRE(
            ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
        BOOST_CHECK_EQUAL(stats.metadata_copied, 0);
        BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    };
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    accept(parent, clock);
    reconcile();
    BOOST_CHECK_EQUAL(stats.prevout_reads, 1);
    BOOST_CHECK_EQUAL(stats.pool_captured, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 1);
    reconcile();
    BOOST_CHECK_EQUAL(stats.prevout_reads, 0);
    BOOST_CHECK_EQUAL(stats.pool_reused, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 0);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().removed, 0);
    accept(child, clock + 1);
    reconcile();
    BOOST_CHECK_EQUAL(stats.prevout_reads, 1);
    BOOST_CHECK_EQUAL(stats.pool_reused, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().retained, 1);
    BOOST_CHECK(
        probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).output_spent);
    g_mempool.removeRecursive(CTransaction(child),
                              MemPoolRemovalReason::EXPIRY);
    reconcile();
    BOOST_CHECK_EQUAL(stats.prevout_reads, 0);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().removed, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 0);
    BOOST_CHECK(
        !probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).output_spent);
    accept(child, clock + 1);
    reconcile();
    g_mempool.removeRecursive(CTransaction(parent),
                              MemPoolRemovalReason::EXPIRY);
    accept(parent, clock + 2);
    accept(child, clock + 1);
    reconcile();
    BOOST_CHECK_EQUAL(stats.pool_reused, 1);
    BOOST_CHECK_EQUAL(stats.prevout_reads, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().removed, 2);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 2);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).time_first_seen,
        clock + 2);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(child).GetId()), 0).time_first_seen,
        clock + 1);
    // Same tip restored, but a different synchronous native generation:
    // cached coins and overlay retention must not cross that boundary.
    CreateAndProcessBlock({}, CScript() << OP_3);
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    reconcile();
    BOOST_CHECK_EQUAL(stats.pool_reused, 0);
    BOOST_CHECK_EQUAL(stats.prevout_reads, 2);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().removed, 2);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 2);
    const auto block =
        CreateAndProcessBlock({parent, child}, CScript() << OP_4);
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
    BOOST_CHECK_EQUAL(stats.metadata_copied, 1);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().removed, 2);
    BOOST_CHECK_EQUAL(probe->pool_apply_stats().added, 0);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).height, 101);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).time_first_seen,
        clock + 2);
    SetMockTime(clock + 3);
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).height, -1);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(child).GetId()), 0).time_first_seen,
        clock + 3);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(incremental_capture_reuses_prefix_and_reads_only_suffix) {
    struct Policy {
        bool original{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = original; }
    } policy;
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-incremental-capture");
    auto probe = ergon_archival::open_archival(root, access);
    ergon_archival::CaptureStats stats;
    auto reconcile = [&] {
        BOOST_REQUIRE(
            ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
        BOOST_CHECK_EQUAL(stats.attempts, 1);
        BOOST_CHECK_EQUAL(stats.discarded_before_db, 0);
    };
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.undo_load_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    const auto before = probe->snapshot().logical_digest;
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.undo_load_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    BOOST_CHECK(probe->snapshot().logical_digest == before);
    // Actual ATMP change with no native chain change must not reread blocks.
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction tx;
    tx.vin.emplace_back(COutPoint(prev.GetId(), 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto hash =
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(tx), 0,
                      SigHashType().withForkId(), Amount::zero());
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(hash, signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    tx.vin[0].scriptSig << signature;
    {
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    }
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    BOOST_CHECK_EQUAL(probe->tx_view(Bytes(CTransaction(tx).GetId()), 0).height,
                      -1);
    g_mempool.removeRecursive(CTransaction(tx), MemPoolRemovalReason::EXPIRY);
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    BOOST_CHECK_THROW(probe->tx_view(Bytes(CTransaction(tx).GetId()), 0),
                      std::exception);
    const auto old_first = CreateAndProcessBlock({}, CScript() << OP_1);
    const auto old_tip = CreateAndProcessBlock({}, CScript() << OP_2);
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 2);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    BOOST_CHECK_EQUAL(probe->snapshot().height, 102);
    {
        CValidationState state;
        CBlockIndex *index;
        {
            LOCK(cs_main);
            index = mapBlockIndex.at(old_first.GetHash());
        }
        BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, index));
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    }
    CreateAndProcessBlock({}, CScript() << OP_3);
    const auto replacement = CreateAndProcessBlock({}, CScript() << OP_4);
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 2);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 101);
    BOOST_CHECK(probe->snapshot().hash == Bytes(replacement.GetHash()));
    BOOST_CHECK(probe->snapshot().hash != Bytes(old_tip.GetHash()));
    {
        CValidationState state;
        BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    }
    reconcile();
    BOOST_CHECK_EQUAL(stats.block_read_calls, 0);
    BOOST_CHECK_EQUAL(stats.undo_load_calls, 0);
    BOOST_CHECK_EQUAL(stats.reused_blocks, 102);
    BOOST_CHECK_EQUAL(probe->snapshot().height, 101);
    BOOST_CHECK(!access.index_failed.load());
    // A separately opened full upstream resync gives the same logical view.
    const auto incremental = probe->snapshot().logical_digest;
    auto fresh = ergon_archival::open_archival(
        Root("archival-full-capture-control"), access);
    ergon_archival::CaptureStats full;
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*fresh, access, {}, {}, &full));
    BOOST_CHECK_EQUAL(full.block_read_calls, 0);
    BOOST_CHECK_EQUAL(full.reused_blocks, 102);
    BOOST_CHECK(fresh->snapshot().logical_digest == incremental);
}
BOOST_AUTO_TEST_CASE(incremental_capture_prefix_owner_and_bounds_fail_closed) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    chronik_bridge::ErgonNodeAccess other(GetConfig(), g_mempool);
    const auto root = Root("archival-prefix-contract");
    auto probe = ergon_archival::open_archival(root, access);
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    const auto inventory = probe->capture_inventory(access);
    BOOST_REQUIRE_EQUAL(inventory.size(), 101);
    BOOST_CHECK_EQUAL(inventory.back().height, 100);
    BOOST_CHECK(inventory.back().wire_bytes > 0);
    BOOST_CHECK_THROW(probe->capture_inventory(other), std::exception);
    auto source = [&] {
        LOCK2(cs_main, g_mempool.cs);
        return ergon_archival::new_capture(
            access, 0, 100, Bytes(ChainActive().Tip()->GetBlockHash()),
            ChainActive().GetSourceGeneration(),
            g_mempool.GetSourceGeneration());
    }();
    BOOST_CHECK_THROW(probe->seed_capture(*source, 102, access),
                      std::exception);
    BOOST_CHECK_THROW(probe->seed_capture(*source, 1, other), std::exception);
    probe->seed_capture(*source, 101, access);
    BOOST_CHECK_THROW(probe->seed_capture(*source, 0, access), std::exception);
    // Invalid seed attempts cannot modify the real DB/index owner.
    BOOST_CHECK_EQUAL(probe->snapshot().height, 100);
    BOOST_CHECK(!access.index_failed.load());
    const auto before = probe->snapshot().logical_digest;
    auto relocated = [&] {
        LOCK2(cs_main, g_mempool.cs);
        return ergon_archival::new_capture(
            access, 0, 100, Bytes(ChainActive().Tip()->GetBlockHash()),
            ChainActive().GetSourceGeneration(),
            g_mempool.GetSourceGeneration());
    }();
    probe->seed_capture(*relocated, 100, access);
    CBlockHeader header;
    BlockHash bound_hash;
    CBlockIndex detached, parent;
    {
        LOCK(cs_main);
        const auto &native = *ChainActive()[100];
        header = native.GetBlockHeader();
        bound_hash = native.GetBlockHash();
        detached.nHeight = 100;
        detached.nStatus = native.nStatus;
        detached.nFile = native.nFile;
        detached.nDataPos = native.nDataPos;
        detached.nUndoPos = native.nUndoPos;
    }
    detached.phashBlock = &bound_hash;
    parent.phashBlock = &header.hashPrevBlock;
    detached.pprev = &parent;
    auto bridge = chronik_bridge::make_bridge(access);
    const auto block = bridge->load_block(detached);
    const auto undo = bridge->load_block_undo(detached);
    const uint64_t charge = ::GetSerializeSize(*block, CLIENT_VERSION) +
                            ::GetSerializeSize(*undo, CLIENT_VERSION);
    // Negative conversion input only. Actual native index/files untouched.
    ++detached.nDataPos;
    relocated->capture_block(*block, *undo, detached, charge, access);
    BOOST_CHECK_EXCEPTION(probe->apply_capture(*relocated, access),
                          std::exception, [](const std::exception &error) {
                              return std::string(error.what())
                                         .find(
                                             "Retained native storage moved") !=
                                     std::string::npos;
                          });
    BOOST_CHECK(probe->snapshot().logical_digest == before);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(unlocked_disk_capture_source_retry_before_db) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-disk-retry");
    auto probe = ergon_archival::open_archival(root, access);
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    int disk = 0, applied = 0;
    const auto old_hash = ChainActive().Tip()->GetBlockHash();
    BOOST_REQUIRE(ergon_archival::reconcile_captured(
        *probe, access, [&] { ++applied; },
        [&] {
            AssertLockNotHeld(cs_main);
            AssertLockNotHeld(g_mempool.cs);
            if (++disk != 1)
                return;
            // ABA: the tip is restored but synchronous generation is not.
            CreateAndProcessBlock({}, CScript() << OP_1);
            CValidationState state;
            BOOST_REQUIRE(
                InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
            BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
            BOOST_CHECK(ChainActive().Tip()->GetBlockHash() == old_hash);
        }));
    BOOST_CHECK_EQUAL(disk, 2);
    BOOST_CHECK_EQUAL(applied, 1); // The stale read never reaches upstream DB.
    BOOST_CHECK_EQUAL(probe->snapshot().height, 100);
    disk = applied = 0;
    BOOST_CHECK(!ergon_archival::reconcile_captured(
        *probe, access, [&] { ++applied; },
        [&] {
            ++disk;
            CreateAndProcessBlock({}, CScript() << OP_2);
        }));
    BOOST_CHECK_EQUAL(disk, 4);
    BOOST_CHECK_EQUAL(applied, 0);
    BOOST_CHECK_THROW(probe->snapshot(), std::exception);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
    BOOST_CHECK_EQUAL(probe->snapshot().height, 104);
    // Metadata drift without a chain generation change must also discard.
    disk = applied = 0;
    DataPositionRestore restore(100);
    BOOST_REQUIRE(ergon_archival::reconcile_captured(
        *probe, access, [&] { ++applied; },
        [&] {
            LOCK(cs_main);
            ++disk;
            if (disk == 1)
                ChainActive()[100]->nDataPos = restore.original + 1;
            else
                ChainActive()[100]->nDataPos = restore.original;
        }));
    BOOST_CHECK_EQUAL(disk, 3);
    BOOST_CHECK_EQUAL(applied, 1);
    BOOST_CHECK_EQUAL(probe->snapshot().height, 104);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(unlocked_disk_capture_stable_fault_is_index_only) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-disk-fault");
    {
        auto probe = ergon_archival::open_archival(root, access);
        BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
        const auto shutdown_before = ShutdownRequested();
        int disk = 0, applied = 0;
        {
            DataPositionRestore restore(100);
            {
                LOCK(cs_main);
                // Corrupt this disposable fixture's metadata before binding.
                // The source then stays stable: it must fail, not spin/retry.
                ChainActive()[100]->nDataPos = restore.original + 1;
            }
            BOOST_CHECK_THROW(
                ergon_archival::reconcile_captured(
                    *probe, access, [&] { ++applied; }, [&] { ++disk; }),
                std::exception);
        }
        BOOST_CHECK_EQUAL(disk, 1);
        BOOST_CHECK_EQUAL(applied, 0);
        BOOST_CHECK(access.index_failed.load());
        BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
        CreateAndProcessBlock({}, CScript() << OP_3);
        BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    }
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK_THROW(ergon_archival::open_archival(root, access),
                      std::exception);
}
BOOST_AUTO_TEST_CASE(unlocked_disk_capture_stop_allows_native_progress) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-disk-stop");
    CallbackLatch disk;
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, {}, [&] {
        AssertLockNotHeld(cs_main);
        AssertLockNotHeld(g_mempool.cs);
        disk.Block();
    });
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(disk.Wait());
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(ChainActive().Height(), 102);
    auto stopping = std::async(std::launch::async, [&] { worker.Stop(); });
    BOOST_CHECK(!worker.WaitThrough(2, 5000));
    BOOST_CHECK(worker.Status().admission_closed);
    BOOST_CHECK(!worker.Status().joined);
    CreateAndProcessBlock({}, CScript() << OP_3);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(ChainActive().Height(), 103);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    disk.Release();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) ==
                  std::future_status::ready);
    stopping.get();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(!worker.Status().failed);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 103);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(unlocked_capture_stop_allows_native_progress) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-unlocked-stop");
    CallbackLatch gap;
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {},
                                          [&] { gap.Block(); });
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(gap.Wait());
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    // DB phase is paused on the worker, not on the validation thread. These
    // actual node operations and their notification fence must still finish.
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(ChainActive().Height(), 102);
    BOOST_CHECK_EQUAL(worker.Status().accepted, 2);
    auto stopping = std::async(std::launch::async, [&] { worker.Stop(); });
    BOOST_CHECK(!worker.WaitThrough(2, 5000));
    BOOST_CHECK(worker.Status().admission_closed);
    BOOST_CHECK(!worker.Status().joined);
    CreateAndProcessBlock({}, CScript() << OP_3);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(ChainActive().Height(), 103);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    gap.Release();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) ==
                  std::future_status::ready);
    stopping.get();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(!worker.Status().failed);
    BOOST_CHECK_EQUAL(worker.Status().completed, 1);
    BOOST_CHECK_EQUAL(worker.Status().discarded, 1);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 103);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(actual_http_ws_runtime_stop_joins_native_users) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-runtime-stop");
    const auto shutdown_before = ShutdownRequested();
    {
        auto probe = ergon_archival::open_archival(root, access);
        const auto before = probe->snapshot().logical_digest;
        const auto result = probe->runtime_probe();
        BOOST_CHECK(result.http_ok);
        BOOST_CHECK(result.websocket_ok);
        BOOST_CHECK(result.blocking_waited);
        BOOST_CHECK(result.concurrent_stop_waited);
        BOOST_CHECK(result.async_dropped);
        BOOST_CHECK(result.server_finished);
        BOOST_CHECK(result.indexer_unique);
        BOOST_CHECK(result.native_refs_released);
        BOOST_CHECK(result.listener_closed);
        BOOST_CHECK(result.websocket_closed);
        BOOST_CHECK(result.partial_http_closed);
        BOOST_CHECK(probe->snapshot().logical_digest == before);
        BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    }
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
    CreateAndProcessBlock({}, CScript() << OP_1);
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 101);
}
BOOST_AUTO_TEST_CASE(ordered_callback_worker_reorg_and_reopen) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-callback-order");
    CallbackLatch latch;
    ergon_archival::ArchivalWorker worker(root, access, 128,
                                          [&] { latch.Block(); });
    BOOST_CHECK_EQUAL(worker.ReadSnapshot().height, 100);
    const auto first = CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(latch.Wait());
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    const auto old_tip = CreateAndProcessBlock({}, CScript() << OP_2);
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    const auto new_tip = CreateAndProcessBlock({}, CScript() << OP_3);
    BOOST_REQUIRE(old_tip.GetHash() != new_tip.GetHash());
    SyncWithValidationInterfaceQueue();
    const auto queued = worker.Status();
    BOOST_CHECK_EQUAL(queued.accepted, 4);
    BOOST_CHECK_EQUAL(queued.completed, 0);
    BOOST_CHECK_EQUAL(queued.pending,
                      4); // Includes the latched in-flight event.
    latch.Release();
    BOOST_REQUIRE(worker.WaitThrough(queued.accepted, 5000));
    const auto caught_up = worker.Status();
    BOOST_CHECK_EQUAL(caught_up.completed, 4);
    BOOST_CHECK_EQUAL(caught_up.pending, 0);
    BOOST_CHECK(caught_up.ready);
    BOOST_CHECK(!caught_up.failed);
    const auto indexed = worker.ReadSnapshot();
    BOOST_CHECK_EQUAL(indexed.height, 102);
    BOOST_CHECK(indexed.hash == Bytes(new_tip.GetHash()));
    BOOST_CHECK_EQUAL(worker.ReadTx(Bytes(first.vtx[0]->GetId()), 0).height,
                      101);
    worker.Stop();
    worker.Stop();
    const auto stopped = worker.Status();
    BOOST_CHECK(stopped.joined);
    BOOST_CHECK(stopped.admission_closed);
    BOOST_CHECK(!stopped.ready);
    BOOST_CHECK_EQUAL(stopped.discarded, 0);
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK(reopened->snapshot().logical_digest == indexed.logical_digest);
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(callback_stop_joins_inflight_and_discards_pending) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-callback-stop");
    const bool shutdown_before = ShutdownRequested();
    CallbackLatch latch;
    ergon_archival::ArchivalWorker worker(root, access, 128,
                                          [&] { latch.Block(); });
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(latch.Wait());
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(worker.Status().accepted, 2);
    auto stopping = std::async(std::launch::async, [&] { worker.Stop(); });
    // A native callback-queue fence ensures Stop can drain without cs_main.
    // WaitThrough wakes on admission closure, not on successful catch-up.
    BOOST_CHECK(!worker.WaitThrough(2, 5000));
    BOOST_CHECK(worker.Status().admission_closed);
    BOOST_CHECK(!worker.Status().joined);
    BOOST_CHECK(stopping.wait_for(std::chrono::milliseconds(0)) ==
                std::future_status::timeout);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    // Actual native mutation while Stop is waiting for the worker. Any queued
    // callback already bound before unregister must see closed admission.
    CreateAndProcessBlock({}, CScript() << OP_3);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(worker.Status().accepted, 2);
    latch.Release();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) ==
                  std::future_status::ready);
    stopping.get();
    const auto stopped = worker.Status();
    BOOST_CHECK(stopped.joined);
    BOOST_CHECK_EQUAL(stopped.completed, 1);
    BOOST_CHECK_EQUAL(stopped.discarded, 1);
    BOOST_CHECK_EQUAL(stopped.pending, 0);
    BOOST_CHECK(!stopped.failed);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 103);
}
BOOST_AUTO_TEST_CASE(callback_overflow_quarantines_only_index) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-callback-overflow");
    const bool shutdown_before = ShutdownRequested();
    CallbackLatch latch;
    ergon_archival::ArchivalWorker worker(root, access, 2,
                                          [&] { latch.Block(); });
    CreateAndProcessBlock({}, CScript() << OP_1);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(latch.Wait());
    CreateAndProcessBlock({}, CScript() << OP_2);
    CreateAndProcessBlock({}, CScript() << OP_3);
    SyncWithValidationInterfaceQueue();
    const auto overflow = worker.Status();
    BOOST_CHECK(overflow.failed);
    BOOST_CHECK(!overflow.ready);
    BOOST_CHECK_EQUAL(overflow.accepted, 2);
    BOOST_CHECK_EQUAL(overflow.pending, 2);
    BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    BOOST_CHECK(!worker.WaitThrough(2, 10));
    latch.Release();
    worker.Stop();
    const auto stopped = worker.Status();
    BOOST_CHECK(stopped.joined);
    BOOST_CHECK_EQUAL(stopped.completed, 0);
    BOOST_CHECK_EQUAL(stopped.discarded, 2);
    BOOST_CHECK_EQUAL(stopped.pending, 0);
    BOOST_CHECK(fs::is_regular_file(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
    // Chronik quarantine must not become node consensus/shutdown authority.
    CreateAndProcessBlock({}, CScript() << OP_4);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 104);
}
BOOST_AUTO_TEST_CASE(callback_stop_drains_subscriber_inflight) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-callback-drain");
    CallbackLatch callback;
    ergon_archival::ArchivalWorker worker(root, access, 128, {},
                                          [&] { callback.Block(); });
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_REQUIRE(callback.Wait());
    auto stopping = std::async(std::launch::async, [&] { worker.Stop(); });
    BOOST_CHECK(!worker.WaitThrough(1, 5000));
    BOOST_CHECK(worker.Status().admission_closed);
    BOOST_CHECK_EQUAL(worker.Status().accepted, 0);
    BOOST_CHECK(stopping.wait_for(std::chrono::milliseconds(0)) ==
                std::future_status::timeout);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    callback.Release();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) ==
                  std::future_status::ready);
    stopping.get();
    const auto stopped = worker.Status();
    BOOST_CHECK(stopped.joined);
    BOOST_CHECK_EQUAL(stopped.accepted, 0);
    BOOST_CHECK_EQUAL(stopped.completed, 0);
    BOOST_CHECK_EQUAL(stopped.rejected, 1);
    BOOST_CHECK_EQUAL(stopped.discarded, 0);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    // After the barrier, the native scheduler cannot call this subscriber.
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(worker.Status().rejected, 1);
    BOOST_CHECK(!access.index_failed.load());
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 102);
}
BOOST_AUTO_TEST_CASE(callback_mempool_parent_child_remove_mine_and_reorg) {
    // The baseline unit fixture omits the daemon's native mempool signal
    // registration. Exercise the actual native connection, not fake callbacks.
    struct MempoolSignals {
        MempoolSignals() {
            GetMainSignals().RegisterWithMempoolSignals(g_mempool);
        }
        ~MempoolSignals() {
            GetMainSignals().UnregisterWithMempoolSignals(g_mempool);
            SyncWithValidationInterfaceQueue();
        }
    } native_signals;
    struct FixturePolicy {
        bool original{fRequireStandard};
        FixturePolicy() { fRequireStandard = false; }
        ~FixturePolicy() { fRequireStandard = original; }
    } policy;
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction parent;
    parent.vin.emplace_back(COutPoint(prev.GetId(), 0));
    parent.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    parent.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 0));
    auto hash = SignatureHash(prev.vout[0].scriptPubKey, CTransaction(parent),
                              0, SigHashType().withForkId(), Amount::zero());
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(hash, signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    parent.vin[0].scriptSig << signature;
    CMutableTransaction child;
    child.vin.emplace_back(COutPoint(CTransaction(parent).GetId(), 0));
    child.vout.emplace_back(Amount::zero(), CScript() << OP_2);
    child.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 1));
    auto accept = [](const CMutableTransaction &tx) {
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    };
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-callback-mempool");
    CallbackLatch latch;
    ergon_archival::ArchivalWorker worker(root, access, 128,
                                          [&] { latch.Block(); });
    accept(parent);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(latch.Wait());
    accept(child);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(worker.Status().accepted, 2);
    BOOST_CHECK_THROW(worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0),
                      std::exception);
    latch.Release();
    BOOST_REQUIRE(worker.WaitThrough(2, 5000));
    const auto parent_view =
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0);
    const auto child_view =
        worker.ReadTx(Bytes(CTransaction(child).GetId()), 0);
    BOOST_CHECK_EQUAL(parent_view.height, -1);
    BOOST_CHECK_EQUAL(parent_view.mempool_input_height, 1);
    BOOST_CHECK(parent_view.mempool_input_coinbase);
    BOOST_CHECK(parent_view.output_spent);
    BOOST_CHECK(parent_view.spender == Bytes(CTransaction(child).GetId()));
    BOOST_CHECK_EQUAL(child_view.mempool_input_height, -1);
    BOOST_CHECK(!child_view.mempool_input_coinbase);
    // A native expiry callback restores the upstream parent's unspent output.
    g_mempool.removeRecursive(CTransaction(child),
                              MemPoolRemovalReason::EXPIRY);
    SyncWithValidationInterfaceQueue();
    auto ticket = worker.Status().accepted;
    BOOST_CHECK_EQUAL(ticket, 3);
    BOOST_REQUIRE(worker.WaitThrough(ticket, 5000));
    BOOST_CHECK(
        !worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).output_spent);
    BOOST_CHECK_THROW(worker.ReadTx(Bytes(CTransaction(child).GetId()), 0),
                      std::exception);
    accept(child);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    const auto parent_time =
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).time_first_seen;
    const auto child_time =
        worker.ReadTx(Bytes(CTransaction(child).GetId()), 0).time_first_seen;
    BOOST_REQUIRE(parent_time > 0 && child_time > 0);
    const auto block =
        CreateAndProcessBlock({parent, child}, prev.vout[0].scriptPubKey);
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    BOOST_CHECK(worker.ReadSnapshot().hash == Bytes(block.GetHash()));
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).height, 101);
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(child).GetId()), 0).height, 101);
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).time_first_seen,
        parent_time);
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(child).GetId()), 0).time_first_seen,
        child_time);
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
    BOOST_CHECK_EQUAL(g_mempool.size(), 2);
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).height, -1);
    BOOST_CHECK_EQUAL(
        worker.ReadTx(Bytes(CTransaction(child).GetId()), 0).height, -1);
    BOOST_CHECK(
        worker.ReadTx(Bytes(CTransaction(parent).GetId()), 0).output_spent);
    worker.Stop();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(real_replay_reopen_queries_and_offline_reorg) {
    const auto &prev = *m_coinbase_txns[0];
    const CScript target = CScript() << OP_1;
    CMutableTransaction spend;
    spend.vin.emplace_back(COutPoint(prev.GetId(), 0));
    spend.vout.emplace_back(prev.vout[0].nValue, target);
    const auto digest =
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(spend), 0,
                      SigHashType().withForkId(), prev.vout[0].nValue);
    std::vector<uint8_t> sig;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(digest, sig));
    sig.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    spend.vin[0].scriptSig << sig;
    const auto accepted =
        CreateAndProcessBlock({spend}, prev.vout[0].scriptPubKey);
    BOOST_REQUIRE_EQUAL(ChainActive().Height(), 101);
    BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash() == accepted.GetHash());
    BOOST_REQUIRE_EQUAL(g_mempool.size(), 0);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-main");
    const auto rebuild = Root("archival-rebuild");
    ergon_archival::Snapshot original{};
    {
        auto probe = ergon_archival::open_archival(root, access);
        original = probe->snapshot();
        BOOST_CHECK_EQUAL(original.height, 101);
        BOOST_CHECK(original.hash == Bytes(accepted.GetHash()));
        BOOST_CHECK_EQUAL(original.num_txs, 103);
        const auto coin = probe->tx_view(Bytes(prev.GetId()), 0);
        BOOST_CHECK(coin.output_spent);
        BOOST_CHECK(coin.spender == Bytes(CTransaction(spend).GetId()));
        BOOST_CHECK_EQUAL(coin.spender_input, 0);
        const auto tx = probe->tx_view(Bytes(CTransaction(spend).GetId()), 0);
        BOOST_CHECK_EQUAL(tx.height, 101);
        BOOST_CHECK_EQUAL(tx.input_value, 0);
        BOOST_CHECK(!tx.output_spent);
        auto script = probe->script_view({target.data(), target.size()});
        BOOST_CHECK_EQUAL(script.confirmed, 1);
        BOOST_CHECK_EQUAL(script.utxos, 1);
        BOOST_CHECK_THROW(ergon_archival::open_archival(root, access),
                          std::exception);
    }
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    {
        auto reopened = ergon_archival::open_archival(root, access);
        auto replay = ergon_archival::open_archival(rebuild, access);
        BOOST_CHECK(reopened->snapshot().logical_digest ==
                    original.logical_digest);
        BOOST_CHECK(replay->snapshot().logical_digest ==
                    original.logical_digest);
    }
    // No callbacks while mutating: boxes and all foreign users are gone.
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    g_mempool
        .clear(); // Mempool reconciliation is explicitly outside this slice.
    const auto alternate = CreateAndProcessBlock({}, CScript() << OP_2);
    BOOST_REQUIRE_EQUAL(ChainActive().Height(), 101);
    BOOST_REQUIRE(alternate.GetHash() != accepted.GetHash());
    {
        auto probe = ergon_archival::open_archival(root, access);
        const auto changed = probe->snapshot();
        BOOST_CHECK(changed.hash == Bytes(alternate.GetHash()));
        BOOST_CHECK(changed.logical_digest != original.logical_digest);
        BOOST_CHECK_EQUAL(changed.num_txs, 102);
        BOOST_CHECK(!probe->tx_view(Bytes(prev.GetId()), 0).output_spent);
        BOOST_CHECK_THROW(probe->tx_view(Bytes(CTransaction(spend).GetId()), 0),
                          std::exception);
        const auto script = probe->script_view({target.data(), target.size()});
        BOOST_CHECK_EQUAL(script.confirmed, 0);
        BOOST_CHECK_EQUAL(script.utxos, 0);
    }
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
}

BOOST_AUTO_TEST_CASE(identity_paths_and_unbound_database_fail_closed) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-bound");
    {
        auto probe = ergon_archival::open_archival(root, access);
        BOOST_CHECK_EQUAL(probe->snapshot().height, 100);
    }
    const auto id_path = fs::path(root) / "ergon-identity.json";
    std::ifstream input(id_path.string(), std::ios::binary);
    const std::string identity((std::istreambuf_iterator<char>(input)), {});
    BOOST_REQUIRE(identity.find("\"network\":\"regtest\"") !=
                  std::string::npos);
    BOOST_REQUIRE(identity.find("adapter_sha256") != std::string::npos);
    {
        std::ofstream output(id_path.string(),
                             std::ios::binary | std::ios::trunc);
        output << "wrong identity\n";
    }
    BOOST_CHECK_THROW(ergon_archival::open_archival(root, access),
                      std::exception);
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!access.index_failed.load());
    const auto unbound = Root("archival-unbound");
    BOOST_REQUIRE(fs::create_directory(fs::path(unbound) / "indexes"));
    BOOST_CHECK_THROW(ergon_archival::open_archival(unbound, access),
                      std::exception);
    BOOST_CHECK(!fs::exists(fs::path(unbound) / "ergon-identity.json"));
    const auto alias = GetDataDir() / "archival-symlink";
    fs::create_directory_symlink(root, alias);
    BOOST_CHECK_THROW(ergon_archival::open_archival(alias.string(), access),
                      std::exception);
    BOOST_CHECK_THROW(ergon_archival::open_archival("relative-root", access),
                      std::exception);
    const auto contaminated = Root("archival-contaminated");
    {
        std::ofstream output((fs::path(contaminated) / "unexpected").string());
        output << "synthetic";
    }
    BOOST_CHECK_THROW(ergon_archival::open_archival(contaminated, access),
                      std::exception);
    BOOST_CHECK(!fs::exists(fs::path(contaminated) / "ergon-identity.json"));
}
BOOST_AUTO_TEST_CASE(source_drift_refuses_stale_view_then_reconciles) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-drift");
    const auto stopped_before = ShutdownRequested();
    {
        auto probe = ergon_archival::open_archival(root, access);
        BOOST_CHECK_EQUAL(probe->snapshot().height, 100);
        // Startup now initializes a captured live view. Ordinary progress
        // refuses stale admission without quarantining a healthy database.
        // Stable disk faults and startup contradictions still quarantine.
        CreateAndProcessBlock({}, CScript() << OP_3);
        BOOST_REQUIRE_EQUAL(ChainActive().Height(), 101);
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        BOOST_CHECK(!access.index_failed.load());
        BOOST_CHECK_EQUAL(ShutdownRequested(), stopped_before);
        BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
        BOOST_REQUIRE(ergon_archival::reconcile_captured(*probe, access));
        BOOST_CHECK_EQUAL(probe->snapshot().height, 101);
        BOOST_CHECK(probe->snapshot().hash ==
                    Bytes(ChainActive().Tip()->GetBlockHash()));
    }
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    chronik_bridge::ErgonNodeAccess fresh_access(GetConfig(), g_mempool);
    {
        auto reopened = ergon_archival::open_archival(root, fresh_access);
        BOOST_CHECK_EQUAL(reopened->snapshot().height, 101);
    }
    BOOST_CHECK(!fresh_access.index_failed.load());
    const auto fresh_root = Root("archival-after-drift");
    {
        auto rebuilt = ergon_archival::open_archival(fresh_root, fresh_access);
        BOOST_CHECK_EQUAL(rebuilt->snapshot().height, 101);
    }
    BOOST_CHECK_EQUAL(ShutdownRequested(), stopped_before);
}
BOOST_AUTO_TEST_CASE(native_barrier_parent_child_mining_reorg_and_conflict) {
    // Inherited zero-subsidy regtest fixture: test nonstandard zero-value
    // mechanics, not a fee-funded standard relay or operator workload.
    struct FixturePolicy {
        bool original{fRequireStandard};
        FixturePolicy() { fRequireStandard = false; }
        ~FixturePolicy() { fRequireStandard = original; }
    } policy;
    auto accept = [](const CMutableTransaction &tx) {
        LOCK(cs_main);
        CValidationState state;
        BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                                 MakeTransactionRef(tx),
                                                 nullptr, true, Amount::zero()),
                              FormatStateMessage(state));
    };
    auto timestamp = [](const TxId &id) {
        for (const auto &entry : g_mempool.infoAll())
            if (entry.tx->GetId() == id)
                return entry.nTime;
        BOOST_FAIL("Expected actual native mempool entry");
        return int64_t(0);
    };
    const auto &prev = *m_coinbase_txns[0];
    const CScript parent_script = CScript() << OP_1;
    const CScript child_script = CScript() << OP_2;
    CMutableTransaction parent;
    parent.vin.emplace_back(COutPoint(prev.GetId(), 0));
    parent.vout.emplace_back(prev.vout[0].nValue, parent_script);
    parent.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 0));
    auto sign = [&](CMutableTransaction &tx) {
        const auto hash =
            SignatureHash(prev.vout[0].scriptPubKey, CTransaction(tx), 0,
                          SigHashType().withForkId(), prev.vout[0].nValue);
        std::vector<uint8_t> sig;
        BOOST_REQUIRE(coinbaseKey.SignECDSA(hash, sig));
        sig.push_back(SIGHASH_ALL | SIGHASH_FORKID);
        tx.vin[0].scriptSig = CScript() << sig;
    };
    sign(parent);
    CMutableTransaction child;
    child.vin.emplace_back(COutPoint(CTransaction(parent).GetId(), 0));
    child.vout.emplace_back(Amount::zero(), child_script);
    child.vout.emplace_back(
        Amount::zero(), CScript() << OP_RETURN << std::vector<uint8_t>(64, 1));
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("archival-native-barrier");
    {
        auto probe = ergon_archival::open_archival(root, access);
        // Explicitly enter the refreshing fixture mode BEFORE native mutation.
        ergon_archival::reconcile_archival(*probe, access);
        accept(parent);
        accept(child);
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 2);
        // Stale views are refused until the barrier commits, not fake-empty.
        BOOST_CHECK_THROW(
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0),
            std::exception);
        BOOST_CHECK(!access.index_failed.load());
        ergon_archival::reconcile_archival(*probe, access);
        auto parent_view =
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0);
        auto child_view = probe->tx_view(Bytes(CTransaction(child).GetId()), 0);
        BOOST_CHECK_EQUAL(parent_view.height, -1);
        BOOST_CHECK_EQUAL(parent_view.mempool_input_height, 1);
        BOOST_CHECK(parent_view.mempool_input_coinbase);
        BOOST_CHECK_EQUAL(child_view.mempool_input_height, -1);
        BOOST_CHECK(!child_view.mempool_input_coinbase);
        BOOST_CHECK(parent_view.output_spent);
        BOOST_CHECK(parent_view.spender == Bytes(CTransaction(child).GetId()));
        BOOST_CHECK_EQUAL(parent_view.time_first_seen,
                          timestamp(CTransaction(parent).GetId()));
        BOOST_CHECK_EQUAL(child_view.time_first_seen,
                          timestamp(CTransaction(child).GetId()));
        BOOST_CHECK_EQUAL(child_view.input_value, 0);
        const auto scripts =
            probe->script_view({parent_script.data(), parent_script.size()});
        BOOST_CHECK_EQUAL(scripts.confirmed, 0);
        BOOST_CHECK_EQUAL(scripts.unconfirmed, 2);
        BOOST_CHECK_EQUAL(scripts.utxos, 0);
        const auto terminal =
            probe->script_view({child_script.data(), child_script.size()});
        BOOST_CHECK_EQUAL(terminal.unconfirmed, 1);
        BOOST_CHECK_EQUAL(terminal.utxos, 1);
        // Native removal restores the parent's unconfirmed UTXO. Admission
        // goes through native validation again; no synthetic entry injection.
        g_mempool.removeRecursive(CTransaction(child),
                                  MemPoolRemovalReason::EXPIRY);
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 1);
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK(!probe->tx_view(Bytes(CTransaction(parent).GetId()), 0)
                         .output_spent);
        BOOST_CHECK_EQUAL(
            probe->script_view({parent_script.data(), parent_script.size()})
                .utxos,
            1);
        BOOST_CHECK_THROW(probe->tx_view(Bytes(CTransaction(child).GetId()), 0),
                          std::exception);
        accept(child);
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK(probe->tx_view(Bytes(CTransaction(parent).GetId()), 0)
                        .output_spent);
        // Same txids and count after remove/re-add still invalidate the view.
        const auto revision = g_mempool.GetTransactionsUpdated();
        g_mempool.removeRecursive(CTransaction(child),
                                  MemPoolRemovalReason::EXPIRY);
        accept(child);
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 2);
        BOOST_CHECK(g_mempool.GetTransactionsUpdated() != revision);
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        BOOST_CHECK(!access.index_failed.load());
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK_EQUAL(probe->tx_view(Bytes(CTransaction(child).GetId()), 0)
                              .time_first_seen,
                          timestamp(CTransaction(child).GetId()));
        // Idempotent refresh must preserve actual upstream query commitments.
        const auto before = probe->snapshot().logical_digest;
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK(probe->snapshot().logical_digest == before);
        const auto mined =
            CreateAndProcessBlock({parent, child}, prev.vout[0].scriptPubKey);
        BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash() == mined.GetHash());
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 0);
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).height, 101);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(child).GetId()), 0).height, 101);
        BOOST_CHECK_EQUAL(
            probe->script_view({parent_script.data(), parent_script.size()})
                .unconfirmed,
            0);
        CValidationState state;
        BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
        BOOST_REQUIRE_EQUAL(ChainActive().Height(), 100);
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 2);
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0).height, -1);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(child).GetId()), 0).height, -1);
        BOOST_CHECK_EQUAL(
            probe->script_view({parent_script.data(), parent_script.size()})
                .unconfirmed,
            2);
        // A different, accepted spend of the same coin removes both the native
        // conflict and its child. No callback reason or fake coin is inferred.
        CMutableTransaction conflict = parent;
        conflict.vout[0].scriptPubKey = CScript() << OP_3;
        sign(conflict);
        const auto alternate =
            CreateAndProcessBlock({conflict}, CScript() << OP_4);
        BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash() ==
                      alternate.GetHash());
        BOOST_REQUIRE_EQUAL(g_mempool.size(), 0);
        ergon_archival::reconcile_archival(*probe, access);
        BOOST_CHECK_THROW(
            probe->tx_view(Bytes(CTransaction(parent).GetId()), 0),
            std::exception);
        BOOST_CHECK_THROW(probe->tx_view(Bytes(CTransaction(child).GetId()), 0),
                          std::exception);
        BOOST_CHECK_EQUAL(
            probe->tx_view(Bytes(CTransaction(conflict).GetId()), 0).height,
            101);
        BOOST_CHECK(probe->tx_view(Bytes(prev.GetId()), 0).spender ==
                    Bytes(CTransaction(conflict).GetId()));
        BOOST_CHECK_EQUAL(
            probe->script_view({parent_script.data(), parent_script.size()})
                .unconfirmed,
            0);
        BOOST_CHECK_EQUAL(
            probe->script_view({child_script.data(), child_script.size()})
                .utxos,
            0);
    }
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(native_barrier_owner_mismatch_isolates_failure) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    chronik_bridge::ErgonNodeAccess wrong_owner(GetConfig(), g_mempool);
    const auto root = Root("archival-wrong-owner");
    const bool stopped_before = ShutdownRequested();
    {
        auto probe = ergon_archival::open_archival(root, access);
        BOOST_CHECK_THROW(
            ergon_archival::reconcile_archival(*probe, wrong_owner),
            std::exception);
        BOOST_CHECK(access.index_failed.load());
        BOOST_CHECK(!wrong_owner.index_failed.load());
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        BOOST_CHECK_EQUAL(ShutdownRequested(), stopped_before);
    }
    BOOST_CHECK(fs::is_regular_file(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK_EQUAL(ShutdownRequested(), stopped_before);
}

BOOST_AUTO_TEST_CASE(owned_native_db_snapshot_outlives_wrapper) {
    std::unique_ptr<CDBSnapshot> snapshot;
    {
        CDBWrapper database(GetDataDir() / "owned-read", 1 << 20, true, false,
                            true);
        BOOST_REQUIRE(database.Write('a', uint64_t{19}));
        BOOST_REQUIRE(database.Write('b', uint8_t{7}));
        snapshot = database.Snapshot();
        BOOST_CHECK(database.OwnsSnapshot(*snapshot));
        BOOST_REQUIRE(database.Write('a', uint64_t{27}));
        BOOST_REQUIRE(database.Erase('a'));
        uint64_t value = 0;
        BOOST_REQUIRE(snapshot->Read('a', value));
        BOOST_CHECK_EQUAL(value, 19);
        BOOST_CHECK(!database.Read('a', value));
    }
    // Includes the obfuscation key, memory environment, cache and logger. A
    // borrowed wrapper/DB would be a use-after-free at this actual native read.
    uint64_t value = 0;
    BOOST_REQUIRE(snapshot->Read('a', value));
    BOOST_CHECK_EQUAL(value, 19);
    BOOST_CHECK(!snapshot->Read('z', value));
    BOOST_CHECK(
        !snapshot->Read('b', value)); // Native decoder rejects truncation.
    CDBWrapper replacement(GetDataDir() / "owned-read", 1 << 20, true);
    BOOST_CHECK(!replacement.OwnsSnapshot(*snapshot));
    snapshot.reset();
    BOOST_REQUIRE(replacement.Write('a', uint64_t{31}));
    BOOST_REQUIRE(replacement.Read('a', value));
    BOOST_CHECK_EQUAL(value, 31);
}

BOOST_AUTO_TEST_CASE(native_coin_snapshot_overlay_and_owner_laws) {
    auto database = std::make_unique<CCoinsViewDB>(1 << 20, true);
    auto cache = std::make_unique<CCoinsViewCache>(database.get());
    const COutPoint point(m_coinbase_txns[0]->GetId(), 0);
    const Coin original(m_coinbase_txns[0]->vout[0], 1, true);
    Coin coin;
    BOOST_CHECK(!cache->CopyCachedCoin(point, coin));
    BOOST_CHECK_EQUAL(cache->GetCacheSize(), 0);
    cache->AddCoin(point, original, false);
    cache->SetBestBlock(ChainActive().Tip()->GetBlockHash());
    BOOST_REQUIRE(cache->Flush());
    auto snapshot = database->Snapshot();
    const auto identity = cache->ReadIdentity();
    BOOST_CHECK(database->OwnsSnapshot(*snapshot));
    BOOST_CHECK(!cache->CopyCachedCoin(point, coin));
    BOOST_CHECK_EQUAL(cache->GetCacheSize(), 0);
    BOOST_REQUIRE(snapshot->GetCoin(point, coin));
    BOOST_CHECK(coin.GetTxOut() == original.GetTxOut());
    BOOST_CHECK_EQUAL(coin.GetHeight(), 1);
    BOOST_CHECK(coin.IsCoinBase());
    BOOST_CHECK_EQUAL(cache->GetCacheSize(), 0);
    // Dirty spent entries shadow an older unspent DB snapshot, even after a
    // flush empties the live cache. CopyCachedCoin never treats them as misses.
    BOOST_REQUIRE(cache->SpendCoin(point));
    BOOST_REQUIRE(cache->CopyCachedCoin(point, coin));
    BOOST_CHECK(coin.IsSpent());
    const Coin tombstone = coin;
    BOOST_REQUIRE(cache->Flush());
    BOOST_CHECK(!cache->CopyCachedCoin(point, coin));
    BOOST_REQUIRE(snapshot->GetCoin(point, coin));
    BOOST_CHECK(!coin.IsSpent());
    BOOST_CHECK(tombstone.IsSpent());
    auto after = database->Snapshot();
    BOOST_CHECK(!after->GetCoin(point, coin));
    cache->AddCoin(point,
                   Coin(CTxOut(Amount::zero(), CScript() << OP_2), 2, false),
                   false);
    BOOST_REQUIRE(cache->CopyCachedCoin(point, coin));
    BOOST_CHECK_EQUAL(coin.GetHeight(), 2);
    BOOST_CHECK(!coin.IsCoinBase());
    BOOST_CHECK(coin.GetTxOut().scriptPubKey == (CScript() << OP_2));
    BOOST_CHECK(!after->GetCoin(point, coin));
    BOOST_REQUIRE(cache->Flush());
    BOOST_CHECK(!after->GetCoin(point, coin));
    cache.reset();
    database.reset();
    BOOST_REQUIRE(snapshot->GetCoin(point, coin));
    BOOST_CHECK(coin.GetTxOut() == original.GetTxOut());
    auto replacement = std::make_unique<CCoinsViewDB>(1 << 20, true);
    CCoinsViewCache next(replacement.get());
    BOOST_CHECK(next.ReadIdentity() != identity);
    BOOST_CHECK(!replacement->OwnsSnapshot(*snapshot));
    BOOST_CHECK(!replacement->OwnsSnapshot(*after));
}

BOOST_AUTO_TEST_CASE(cold_chainstate_capture_retries_owner_and_aba) {
    struct Policy {
        bool original{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = original; }
    } policy;
    BOOST_REQUIRE(FileBackedFixtureChainstate());
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    auto probe = ergon_archival::open_archival(Root("cold-owner-aba"), access);
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction spend;
    spend.vin.emplace_back(COutPoint(prev.GetId(), 0));
    spend.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(spend), 0,
                      SigHashType().withForkId(), Amount::zero()),
        signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    spend.vin[0].scriptSig << signature;
    {
        LOCK2(cs_main, g_mempool.cs);
        CValidationState state;
        BOOST_REQUIRE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                         MakeTransactionRef(spend), nullptr,
                                         true, Amount::zero()));
        BOOST_REQUIRE(pcoinsTip->Flush());
        Coin coin;
        BOOST_CHECK(!pcoinsTip->CopyCachedCoin(spend.vin[0].prevout, coin));
    }
    const auto hash = ChainActive().Tip()->GetBlockHash();
    const auto generation = ChainActive().GetSourceGeneration();
    unsigned reads = 0;
    ergon_archival::CaptureStats stats;
    BOOST_REQUIRE(ergon_archival::reconcile_captured(
        *probe, access, {},
        [&] {
            AssertLockNotHeld(cs_main);
            AssertLockNotHeld(g_mempool.cs);
            if (++reads == 1) {
                LOCK(cs_main);
                // Same hash/generation/DB, new cache owner: scalar source
                // checks alone must NOT accept this detached capture.
                pcoinsTip =
                    std::make_unique<CCoinsViewCache>(pcoinsdbview.get());
            } else if (reads == 2) {
                CreateAndProcessBlock({}, CScript() << OP_2);
                LOCK(cs_main);
                CValidationState state;
                BOOST_REQUIRE(
                    InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
                BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
                BOOST_REQUIRE(pcoinsTip->Flush());
            }
        },
        &stats));
    BOOST_CHECK_EQUAL(reads, 3);
    BOOST_CHECK_EQUAL(stats.attempts, 3);
    BOOST_CHECK_EQUAL(stats.discarded_before_db, 2);
    BOOST_CHECK_EQUAL(stats.cold_prevout_reads, 3);
    BOOST_CHECK(ChainActive().Tip()->GetBlockHash() == hash);
    BOOST_CHECK(ChainActive().GetSourceGeneration() > generation);
    BOOST_CHECK_EQUAL(
        probe->tx_view(Bytes(CTransaction(spend).GetId()), 0).height, -1);
    BOOST_CHECK_EQUAL(g_mempool.size(), 1);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_REQUIRE(
        ergon_archival::reconcile_captured(*probe, access, {}, {}, &stats));
    BOOST_CHECK_EQUAL(stats.cold_prevout_reads, 0);
    BOOST_CHECK_EQUAL(stats.pool_reused, 1);
}

BOOST_AUTO_TEST_CASE(cold_chainstate_worker_stop_allows_native_progress) {
    struct Policy {
        bool original{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = original; }
    } policy;
    BOOST_REQUIRE(FileBackedFixtureChainstate());
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("cold-worker-stop");
    CallbackLatch cold;
    std::atomic<bool> first{true};
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, {}, [&] {
        AssertLockNotHeld(cs_main);
        AssertLockNotHeld(g_mempool.cs);
        if (first.exchange(false)) cold.Block();
    });
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction spend;
    spend.vin.emplace_back(COutPoint(prev.GetId(), 0));
    spend.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(spend), 0,
                      SigHashType().withForkId(), Amount::zero()),
        signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    spend.vin[0].scriptSig << signature;
    {
        LOCK2(cs_main, g_mempool.cs);
        CValidationState state;
        BOOST_REQUIRE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                         MakeTransactionRef(spend), nullptr,
                                         true, Amount::zero()));
        BOOST_REQUIRE(pcoinsTip->Flush());
        Coin coin;
        BOOST_CHECK(!pcoinsTip->CopyCachedCoin(spend.vin[0].prevout, coin));
    }
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(cold.Wait());
    BOOST_CHECK_THROW(worker.ReadSnapshot(), std::exception);
    // Actual block validation and callback drain progress while an owned cold
    // read session is paused; no native lock or coin-cache reference is held.
    CreateAndProcessBlock({}, CScript() << OP_2);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    auto stopping = std::async(std::launch::async, [&] { worker.Stop(); });
    BOOST_CHECK(!worker.WaitThrough(1, 5000));
    BOOST_CHECK(worker.Status().admission_closed);
    BOOST_CHECK(!worker.Status().joined);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.lock"));
    cold.Release();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) ==
                  std::future_status::ready);
    stopping.get();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(!worker.Status().failed);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    auto reopened = ergon_archival::open_archival(root, access);
    BOOST_CHECK_EQUAL(
        reopened->tx_view(Bytes(CTransaction(spend).GetId()), 0).height, -1);
}

BOOST_AUTO_TEST_CASE(spent_cache_tombstone_quarantines_only_index) {
    struct Policy {
        bool original{fRequireStandard};
        Policy() { fRequireStandard = false; }
        ~Policy() { fRequireStandard = original; }
    } policy;
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("spent-overlay-fault");
    {
        auto probe = ergon_archival::open_archival(root, access);
        const auto stopped = ShutdownRequested();
        const auto &prev = *m_coinbase_txns[0];
        CMutableTransaction spend;
        spend.vin.emplace_back(COutPoint(prev.GetId(), 0));
        spend.vout.emplace_back(Amount::zero(), CScript() << OP_1);
        std::vector<uint8_t> signature;
        BOOST_REQUIRE(coinbaseKey.SignECDSA(
            SignatureHash(prev.vout[0].scriptPubKey, CTransaction(spend), 0,
                          SigHashType().withForkId(), Amount::zero()),
            signature));
        signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
        spend.vin[0].scriptSig << signature;
        {
            LOCK2(cs_main, g_mempool.cs);
            CValidationState state;
            BOOST_REQUIRE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
                                             MakeTransactionRef(spend), nullptr,
                                             true, Amount::zero()));
            BOOST_REQUIRE(pcoinsTip->Flush());
            // Explicit fixture corruption, NOT an accepted native transition:
            // the pool references a spent overlay hiding an unspent DB value.
            BOOST_REQUIRE(pcoinsTip->SpendCoin(spend.vin[0].prevout));
            Coin tombstone, backing;
            BOOST_REQUIRE(
                pcoinsTip->CopyCachedCoin(spend.vin[0].prevout, tombstone));
            BOOST_CHECK(tombstone.IsSpent());
            BOOST_REQUIRE(pcoinsdbview->Snapshot()->GetCoin(
                spend.vin[0].prevout, backing));
            BOOST_CHECK(!backing.IsSpent());
        }
        BOOST_CHECK_THROW(ergon_archival::reconcile_captured(*probe, access),
                          std::exception);
        BOOST_CHECK_THROW(probe->snapshot(), std::exception);
        BOOST_CHECK(access.index_failed.load());
        BOOST_CHECK_EQUAL(ShutdownRequested(), stopped);
        BOOST_CHECK_EQUAL(ChainActive().Height(), 100);
        BOOST_CHECK_EQUAL(g_mempool.size(), 1);
    }
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    // Native fixture teardown discards this deliberate corrupt overlay.
}
#ifdef CHRONIK_ERGON_PLUGIN_TESTS
namespace {
struct PublicPluginSignals {
    bool standard{fRequireStandard};
    PublicPluginSignals() {
        fRequireStandard = false;
        GetMainSignals().RegisterWithMempoolSignals(g_mempool);
    }
    ~PublicPluginSignals() {
        GetMainSignals().UnregisterWithMempoolSignals(g_mempool);
        SyncWithValidationInterfaceQueue();
        fRequireStandard = standard;
    }
};
void AcceptPublicPluginTx(const CMutableTransaction &tx) {
    LOCK(cs_main);
    CValidationState state;
    BOOST_REQUIRE_MESSAGE(AcceptToMemoryPool(GetConfig(), g_mempool, state,
        MakeTransactionRef(tx), nullptr, true, Amount::zero()), FormatStateMessage(state));
}
bool PublicPluginReadUnavailable(uint16_t port) {
    try {
        const auto reply = Http(port, "/blockchain-info");
        // A fatal worker fault tears down its HTTP owner; canonical refusal
        // before teardown or connection closure are both fail-closed. A
        // malformed response, timeout or successful stale body never passes.
        return reply.status == 0 || (reply.status == 503 &&
            ergon_archival::http_error_matches(Body(reply), "503: Ergon index view unavailable"));
    } catch (const boost::system::system_error &error) {
        return error.code() == boost::asio::error::connection_refused ||
               error.code() == boost::asio::error::connection_reset ||
               error.code() == boost::asio::error::broken_pipe;
    }
}
}
BOOST_AUTO_TEST_CASE(upstream_public_plugin_lifecycle) {
    PublicPluginSignals signals;
    auto plugins = ergon_archival::make_plugin_fixture(Root("plugin-source"), 0);
    BOOST_REQUIRE_EQUAL(plugins->loaded(), 1);
    // Never create a second Python interpreter context in this process.
    BOOST_CHECK_THROW(ergon_archival::make_plugin_fixture(Root("duplicate-plugin"), 0), std::exception);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("plugin-index");
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction parent, child;
    parent.vin.emplace_back(COutPoint(prev.GetId(), 0));
    auto alp = ergon_archival::fixture_alp_script(0, {});
    CScript metadata(alp.data(), alp.data() + alp.size());
    metadata << std::vector<uint8_t>{'E','R','G','N'};
    parent.vout.emplace_back(Amount::zero(), metadata);
    parent.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    parent.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(SignatureHash(prev.vout[0].scriptPubKey,
        CTransaction(parent), 0, SigHashType().withForkId(), Amount::zero()), signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    parent.vin[0].scriptSig << signature;
    child.vin.emplace_back(COutPoint(CTransaction(parent).GetId(), 1));
    child.vout.emplace_back(Amount::zero(), CScript() << OP_RETURN
        << std::vector<uint8_t>{'E','R','G','N'} << std::vector<uint8_t>(64, 0));
    child.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    const auto parent_id = CTransaction(parent).GetId();
    const auto child_id = CTransaction(child).GetId();
    std::vector<uint8_t> ids(parent_id.begin(), parent_id.end());
    ids.insert(ids.end(), child_id.begin(), child_id.end());
    std::vector<uint8_t> points(parent_id.begin(), parent_id.end());
    points.insert(points.end(), {2,0,0,0});
    points.insert(points.end(), child_id.begin(), child_id.end());
    points.insert(points.end(), {1,0,0,0});
    {
        ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, {}, {}, &*plugins);
        const auto port = worker.StartHttpFixture();
        AcceptPublicPluginTx(parent);
        AcceptPublicPluginTx(child);
        SyncWithValidationInterfaceQueue();
        BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
        auto check = [&] {
            auto p = Http(port, "/tx/" + parent_id.ToString());
            auto c = Http(port, "/tx/" + child_id.ToString());
            BOOST_REQUIRE_EQUAL(p.status, 200);
            BOOST_REQUIRE_EQUAL(c.status, 200);
            BOOST_CHECK(ergon_archival::http_plugin_tx_matches(Body(p), 1, 0, 1000));
            BOOST_CHECK(ergon_archival::http_plugin_tx_matches(Body(c), 1, 1, 0));
            auto groups = Http(port, "/plugin/ergon_public_probe/groups?page_size=10");
            BOOST_REQUIRE_EQUAL(groups.status, 200);
            BOOST_CHECK(ergon_archival::http_plugin_group_matches(Body(groups), 0, {}));
            auto utxos = Http(port, "/plugin/ergon_public_probe/7075626c6963/utxos");
            BOOST_REQUIRE_EQUAL(utxos.status, 200);
            BOOST_CHECK(ergon_archival::http_plugin_group_matches(Body(utxos), 1, {points.data(), points.size()}));
            auto history = Http(port, "/plugin/ergon_public_probe/7075626c6963/history");
            BOOST_REQUIRE_EQUAL(history.status, 200);
            const std::array<uint64_t,3> expected{2,1,0};
            BOOST_CHECK(ergon_archival::http_fixture_check(5, Body(history), {ids.data(), ids.size()}, {expected.data(), expected.size()}));
        };
        check();
        auto pending = Http(port, "/plugin/ergon_public_probe/7075626c6963/unconfirmed-txs");
        BOOST_REQUIRE_EQUAL(pending.status, 200);
        const std::array<uint64_t,3> both{2,1,0};
        BOOST_CHECK(ergon_archival::http_fixture_check(5, Body(pending), {ids.data(), ids.size()}, {both.data(), both.size()}));
        CreateAndProcessBlock({parent, child}, prev.vout[0].scriptPubKey);
        SyncWithValidationInterfaceQueue();
        BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
        BOOST_CHECK_EQUAL(worker.ReadTx(Bytes(child_id), 1).height, 101);
        check();
        auto confirmed = Http(port, "/plugin/ergon_public_probe/7075626c6963/confirmed-txs");
        BOOST_REQUIRE_EQUAL(confirmed.status, 200);
        BOOST_CHECK(ergon_archival::http_fixture_check(5, Body(confirmed), {ids.data(), ids.size()}, {both.data(), both.size()}));
        CValidationState state;
        BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, ChainActive().Tip()));
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
        SyncWithValidationInterfaceQueue();
        BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted, 5000));
        BOOST_CHECK_EQUAL(g_mempool.size(), 2);
        check();
        worker.Stop();
        BOOST_CHECK(worker.Status().joined);
        BOOST_CHECK(!access.index_failed.load());
        BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    }
    // Share the existing owned Python/plugin context, never reinitialize it.
    auto reopened = ergon_archival::open_archival_with_plugins(root, access, *plugins);
    const auto port = reopened->start_http_fixture();
    auto response = Http(port, "/tx/" + child_id.ToString());
    BOOST_REQUIRE_EQUAL(response.status, 200);
    BOOST_CHECK(ergon_archival::http_plugin_tx_matches(Body(response), 1, 1, 0));
    BOOST_CHECK_THROW(ergon_archival::open_archival(root, access), std::exception);
    reopened->stop_http_fixture();
}

BOOST_AUTO_TEST_CASE(upstream_public_plugin_exception_is_index_only) {
    PublicPluginSignals signals;
    auto plugins = ergon_archival::make_plugin_fixture(Root("throw-plugin-source"), 1);
    BOOST_REQUIRE_EQUAL(plugins->loaded(), 1);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("throw-plugin-index");
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, {}, {}, &*plugins);
    const auto port = worker.StartHttpFixture();
    BOOST_REQUIRE_EQUAL(Http(port, "/blockchain-info").status, 200);
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction tx;
    tx.vin.emplace_back(COutPoint(prev.GetId(), 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_RETURN
        << std::vector<uint8_t>{'E','R','G','N'} << std::vector<uint8_t>(64, 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(SignatureHash(prev.vout[0].scriptPubKey,
        CTransaction(tx), 0, SigHashType().withForkId(), Amount::zero()), signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    tx.vin[0].scriptSig << signature;
    const auto shutdown = ShutdownRequested();
    AcceptPublicPluginTx(tx);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK(!worker.WaitThrough(worker.Status().accepted, 5000));
    BOOST_CHECK(worker.Status().failed);
    BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK(PublicPluginReadUnavailable(port));
    BOOST_CHECK_EQUAL(g_mempool.size(), 1);
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
    CreateAndProcessBlock({}, CScript() << OP_2);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    worker.Stop();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(upstream_public_plugin_invalid_output_is_index_only) {
    PublicPluginSignals signals;
    auto plugins = ergon_archival::make_plugin_fixture(Root("bad-plugin-source"), 2);
    BOOST_REQUIRE_EQUAL(plugins->loaded(), 1);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("bad-plugin-index");
    ergon_archival::ArchivalWorker worker(root, access, 128, {}, {}, {}, {}, &*plugins);
    const auto port = worker.StartHttpFixture();
    BOOST_REQUIRE_EQUAL(Http(port, "/blockchain-info").status, 200);
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction tx;
    tx.vin.emplace_back(COutPoint(prev.GetId(), 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_RETURN
        << std::vector<uint8_t>{'E','R','G','N'} << std::vector<uint8_t>(64, 0));
    tx.vout.emplace_back(Amount::zero(), CScript() << OP_1);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(SignatureHash(prev.vout[0].scriptPubKey,
        CTransaction(tx), 0, SigHashType().withForkId(), Amount::zero()), signature));
    signature.push_back(SIGHASH_ALL | SIGHASH_FORKID);
    tx.vin[0].scriptSig << signature;
    const auto shutdown = ShutdownRequested();
    AcceptPublicPluginTx(tx);
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK(!worker.WaitThrough(worker.Status().accepted, 5000));
    BOOST_CHECK(worker.Status().failed);
    BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK(PublicPluginReadUnavailable(port));
    BOOST_CHECK_EQUAL(g_mempool.size(), 1);
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
    CreateAndProcessBlock({}, CScript() << OP_2);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    worker.Stop();
    BOOST_CHECK(worker.Status().joined);
    BOOST_CHECK(fs::exists(fs::path(root) / "probe.failed"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
}
BOOST_AUTO_TEST_CASE(upstream_public_plugin_source_and_config_drift_refused) {
    const auto source = Root("drift-plugin-source");
    BOOST_CHECK_THROW(ergon_archival::make_plugin_fixture(source, 99), std::exception);
    auto plugins = ergon_archival::make_plugin_fixture(source, 0);
    BOOST_REQUIRE_EQUAL(plugins->loaded(), 1);
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto root = Root("drift-plugin-index");
    const auto config = fs::path(source) / "plugins.toml";
    std::ifstream original_file(config.string());
    const std::string original((std::istreambuf_iterator<char>(original_file)),
                               std::istreambuf_iterator<char>());
    {
        std::ofstream altered(config.string(), std::ios::app);
        altered << "# unexpected configuration drift\n";
    }
    BOOST_CHECK_THROW(ergon_archival::open_archival_with_plugins(root, access, *plugins), std::exception);
    BOOST_CHECK(!fs::exists(fs::path(root) / "indexes"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK_EQUAL(ChainActive().Height(), 100);
    {
        std::ofstream restored(config.string(), std::ios::trunc);
        restored << original;
    }
    {
        std::ofstream altered((fs::path(source) / "ergon_public_probe.py").string(), std::ios::app);
        altered << "# unexpected public source drift\n";
    }
    BOOST_CHECK_THROW(ergon_archival::open_archival_with_plugins(root, access, *plugins), std::exception);
    BOOST_CHECK(!fs::exists(fs::path(root) / "indexes"));
    BOOST_CHECK(!fs::exists(fs::path(root) / "probe.lock"));
    BOOST_CHECK(!access.index_failed.load());
}
#else
BOOST_AUTO_TEST_CASE(upstream_public_plugin_disabled_refuses_setup) {
    const auto source = Root("disabled-plugin-source");
    const auto shutdown = ShutdownRequested();
    BOOST_CHECK_THROW(ergon_archival::make_plugin_fixture(source, 0), std::exception);
    BOOST_CHECK(fs::is_empty(fs::path(source)));
    BOOST_CHECK_EQUAL(ChainActive().Height(), 100);
    BOOST_CHECK_EQUAL(g_mempool.size(), 0);
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
}
#endif
BOOST_AUTO_TEST_SUITE_END()
