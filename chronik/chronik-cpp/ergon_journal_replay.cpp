// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_journal_replay.h>
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik-cpp/chronik_bridge.h>
#include <chronik-bridge/src/ffi.rs.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <clientversion.h>
#include <shutdown.h>
#include <undo.h>
#include <validation.h>
#include <validationinterface.h>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <vector>

namespace ergon_archival {
namespace {
template<class H> std::array<uint8_t,32> HashBytes(const H &hash) {
    std::array<uint8_t,32> out;
    std::copy_n(hash.begin(),out.size(),out.begin());return out;
}
struct StorageRef {
    CBlockHeader header;
    int height,file;
    unsigned data,undo;
    BlockStatus status;
};
}
struct JournalReplay::Impl {
    ArchivalProbe &probe;
    const chronik_bridge::ErgonNodeAccess &node;
    const std::shared_ptr<ValidationEventJournal> journal;
    rust::Box<RequestGate> gate;
    mutable std::mutex mu;
    bool attached{false},stopped{false};
    // Same bounded recent-storage guard as the existing retained profile,
    // not a whole-history nonrelocation proof. Positioned readers still bind
    // actual native storage on every later tx read.
    std::deque<StorageRef> recent;
    Impl(ArchivalProbe &p,const chronik_bridge::ErgonNodeAccess &n,size_t events,size_t bytes,
         bool fence_held=false)
        :probe(p),node(n),journal(std::make_shared<ValidationEventJournal>(events,bytes)),
         gate(p.request_gate()) {
        if(fence_held) Attach();
        else {
            AssertLockNotHeld(cs_main);AssertLockNotHeld(node.mempool.cs);
            LOCK2(cs_main,node.mempool.cs);Attach();
        }
    }
    void Attach() {
        AssertLockHeld(cs_main);AssertLockHeld(node.mempool.cs);
        probe.assert_current_capture(node); // Scalar source fence, never DB.
        if (!GetMainSignals().AttachEventJournal(journal))
            throw std::runtime_error("Native journal already owned");
        attached=true;
        try { probe.begin_journal(node); }
        catch (...) {
            GetMainSignals().DetachEventJournal(journal);
            attached=false;throw;
        }
    }
    void Healthy() const {
        const auto status=journal->GetStatus();
        if (stopped || status.closed || status.failure!=ValidationEventJournal::Failure::None ||
            node.index_failed.load())
            throw std::runtime_error("Native journal continuity unavailable");
        // Genuine continuity/index failure wins even during shutdown. A
        // cancellation is thrown only at an explicit healthy event boundary.
        if(ShutdownRequested()) throw JournalReplayCancelled{};
    }
    void CheckStorage(const StorageRef &ref) const {
        AssertLockHeld(cs_main);AssertLockHeld(node.mempool.cs);
        archival_reconcile_guard(node);
        const auto *index=LookupBlockIndex(ref.header.GetHash());
        if (!index || index->nHeight!=ref.height ||
            index->GetBlockHeader().GetHash()!=ref.header.GetHash() ||
            index->GetBlockPos()!=FlatFilePos(ref.file,ref.data) ||
            index->GetUndoPos()!=FlatFilePos(ref.file,ref.undo))
            throw std::runtime_error("Native journal storage changed");
    }
    void Fail() {
        gate->fail();
        node.index_failed.store(true);
        probe.fail_reconcile();
    }
    void Apply(const ValidationEventJournal::Event &event) {
        using Kind=ValidationEventJournal::Kind;
        if(event.sequence!=probe.journal_sequence()+1)
            throw std::runtime_error("Native journal ordinal gap");
        if(event.kind==Kind::Added) {
            probe.apply_journal_add(event.sequence,*event.tx,event.spent_coins,event.accept_time,node);
        } else if(event.kind==Kind::Removed) {
            probe.apply_journal_remove(event.sequence,HashBytes(event.tx->GetId()),node);
        } else {
            if(event.height<=0 || event.file<0 || !event.data_pos || !event.undo_pos)
                throw std::runtime_error("Incomplete journal block storage");
            const StorageRef ref{event.header,event.height,event.file,event.data_pos,
                                 event.undo_pos,event.status};
            { LOCK2(cs_main,node.mempool.cs);CheckStorage(ref); }
            CBlockIndex index(ref.header),parent;
            const auto hash=ref.header.GetHash();
            index.phashBlock=&hash;
            parent.phashBlock=&ref.header.hashPrevBlock;index.pprev=&parent;
            index.nHeight=ref.height;index.nFile=ref.file;index.nDataPos=ref.data;
            index.nUndoPos=ref.undo;index.nStatus=ref.status;
            auto bridge=chronik_bridge::make_bridge(node);
            auto block=bridge->load_block(index);
            auto undo=bridge->load_block_undo(index);
            const auto block_bytes=::GetSerializeSize(*block,CLIENT_VERSION);
            const auto undo_bytes=::GetSerializeSize(*undo,CLIENT_VERSION);
            if(block_bytes>64*1024*1024 || undo_bytes>64*1024*1024-block_bytes)
                throw std::runtime_error("Journal block working payload budget");
            { LOCK2(cs_main,node.mempool.cs);CheckStorage(ref);Healthy(); }
            rust::Vec<uint8_t> conflicts;
            for(const auto &id:event.conflicts)
                for(auto byte:HashBytes(id)) conflicts.push_back(byte);
            probe.apply_journal_block(event.sequence,event.kind==Kind::Connected,*block,*undo,
                index,rust::Slice<const uint8_t>(conflicts.data(),conflicts.size()),node);
            recent.push_back(ref);
            if(recent.size()>288) recent.pop_front();
        }
    }
    uint64_t Drain() {
        AssertLockNotHeld(cs_main);AssertLockNotHeld(node.mempool.cs);
        std::lock_guard<std::mutex> lock(mu);
        try {
            Healthy();
            const auto target=journal->GetStatus().accepted;
            uint64_t count=0;
            while(probe.journal_sequence()<target) {
                Healthy();
                auto event=journal->Take();
                if(!event) throw std::runtime_error("Native journal missing event");
                Apply(*event);++count; // Lease remains charged throughout DB work.
            }
            Healthy();return count;
        } catch (const JournalReplayCancelled &) { gate->close();throw; }
        catch (...) { Fail();throw; }
    }
    bool Publish() {
        AssertLockNotHeld(cs_main);AssertLockNotHeld(node.mempool.cs);
        std::lock_guard<std::mutex> lock(mu);
        try {
            Healthy();
            const auto sequence=probe.journal_sequence();
            int height;BlockHash hash;uint64_t generation,revision;
            std::vector<JournalPoolEntry> pool;
            auto source=[&] {
                LOCK2(cs_main,node.mempool.cs);
                archival_reconcile_guard(node);Healthy();
                if(journal->GetStatus().accepted!=sequence) return std::unique_ptr<rust::Box<CapturedSource>>{};
                for(const auto &ref:recent) CheckStorage(ref);
                height=ChainActive().Height();hash=ChainActive().Tip()->GetBlockHash();
                generation=archival_chain_generation(node);revision=archival_mempool_revision(node);
                for(const auto &entry:node.mempool.infoAll())
                    pool.push_back(JournalPoolEntry{HashBytes(entry.tx->GetId()),entry.nTime});
                return std::make_unique<rust::Box<CapturedSource>>(new_capture(node,height,height,
                    HashBytes(hash),generation,revision));
            }();
            if(!source) return false;
            probe.prepare_journal_publication(sequence,**source,
                rust::Slice<const JournalPoolEntry>(pool.data(),pool.size()),node);
            LOCK2(cs_main,node.mempool.cs);
            Healthy();archival_reconcile_guard(node);
            for(const auto &ref:recent) CheckStorage(ref);
            if(journal->GetStatus().accepted!=sequence ||
                ChainActive().Height()!=height || ChainActive().Tip()->GetBlockHash()!=hash ||
                archival_chain_generation(node)!=generation ||
                archival_mempool_revision(node)!=revision) return false;
            return probe.publish_capture(**source,node);
        } catch (const JournalReplayCancelled &) { gate->close();throw; }
        catch (...) { Fail();throw; }
    }
    void Stop() {
        AssertLockNotHeld(cs_main);AssertLockNotHeld(node.mempool.cs);
        gate->close(); // No DB/native lock while closing request/session admission.
        std::lock_guard<std::mutex> lock(mu);
        if(stopped) return;
        if(attached) {
            LOCK2(cs_main,node.mempool.cs);
            if(!GetMainSignals().DetachEventJournal(journal)) {
                // Do not throw from the joined-owner destructor, nor detach a
                // different observer. This consumer stays permanently closed.
                node.index_failed.store(true);
                gate->fail();
            }
            attached=false;
        }
        journal->Close();stopped=true;
    }
};
JournalReplay::JournalReplay(ArchivalProbe &p,const chronik_bridge::ErgonNodeAccess &n,
                             size_t events,size_t bytes)
    :impl(std::make_unique<Impl>(p,n,events,bytes)) {}
JournalReplay::JournalReplay(std::unique_ptr<Impl> state):impl(std::move(state)) {}
std::unique_ptr<JournalReplay> JournalReplay::AnchorUnderNativeFence(
    ArchivalProbe &probe,const chronik_bridge::ErgonNodeAccess &node,
    size_t events,size_t bytes) {
    AssertLockHeld(cs_main);AssertLockHeld(node.mempool.cs);
    return std::unique_ptr<JournalReplay>(new JournalReplay(
        std::make_unique<Impl>(probe,node,events,bytes,true)));
}
JournalReplay::~JournalReplay() { impl->Stop(); }
uint64_t JournalReplay::Drain() { return impl->Drain(); }
bool JournalReplay::Publish() { return impl->Publish(); }
ValidationEventJournal::Status JournalReplay::Status() const { return impl->journal->GetStatus(); }
void JournalReplay::Stop() { impl->Stop(); }
}
