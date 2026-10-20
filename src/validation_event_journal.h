// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef BITCOIN_VALIDATION_EVENT_JOURNAL_H
#define BITCOIN_VALIDATION_EVENT_JOURNAL_H

#include <chain.h>
#include <coins.h>
#include <core_memusage.h>
#include <atomic>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>

// Native observer data only. Never a validator, acceptance decision or DB.
// Block entries retain immutable disk/header metadata, not block payloads or
// mutable CBlockIndex pointers. The eventual reader must validate storage.
class ValidationEventJournal {
public:
    enum class Kind { Added, Removed, Connected, Disconnected };
    enum class Failure { None, Capacity, Bytes, Sequence, MissingCoin,
                         InvalidPayload, Allocation };
private:
    struct Budget {
        std::atomic<size_t> events{0}, bytes{0};
    };
    struct Charge {
        const std::shared_ptr<Budget> budget;
        const size_t bytes;
        Charge(std::shared_ptr<Budget> b, size_t n) : budget(std::move(b)), bytes(n) {
            budget->events.fetch_add(1);
            budget->bytes.fetch_add(bytes);
        }
        ~Charge() {
            budget->bytes.fetch_sub(bytes);
            budget->events.fetch_sub(1);
        }
    };
public:
    struct Event {
        // Declared first: released AFTER all owned payload members are freed.
        const std::shared_ptr<Charge> charge;
        uint64_t sequence{0};
        Kind kind;
        CTransactionRef tx;
        std::vector<Coin> spent_coins;
        int64_t accept_time{0};
        CBlockHeader header;
        int height{-1}, file{-1};
        unsigned data_pos{0}, undo_pos{0};
        BlockStatus status;
        std::vector<TxId> conflicts;
        explicit Event(std::shared_ptr<Charge> c, Kind k)
            : charge(std::move(c)), kind(k) {}
        Event(const Event &) = delete;
        Event &operator=(const Event &) = delete;
    };
    struct Status {
        uint64_t accepted;
        size_t queued, retained_events, retained_bytes;
        Failure failure;
        bool closed;
    };
private:
    const size_t max_events, max_bytes;
    const std::shared_ptr<Budget> budget{std::make_shared<Budget>()};
    mutable std::mutex mu;
    std::vector<std::shared_ptr<const Event>> slots;
    size_t head{0}, queued{0};
    uint64_t accepted{0};
    Failure failure{Failure::None};
    bool closed{false};

    static size_t CheckedEvents(size_t events) {
        if (!events || events > 4096)
            throw std::invalid_argument("Invalid native observer journal capacity");
        return events;
    }
    void Fail(Failure why) noexcept {
        if (failure == Failure::None) failure = why;
    }
    bool Reserve(size_t bytes) noexcept {
        if (closed || failure != Failure::None) return false;
        if (accepted == std::numeric_limits<uint64_t>::max()) {
            Fail(Failure::Sequence); return false;
        }
        // Outstanding consumer leases still count, even after Take().
        if (budget->events.load() >= max_events) {
            Fail(Failure::Capacity); return false;
        }
        if (bytes > max_bytes || budget->bytes.load() > max_bytes - bytes) {
            Fail(Failure::Bytes); return false;
        }
        return true;
    }
    void Commit(std::shared_ptr<Event> event) noexcept {
        event->sequence = ++accepted;
        slots[(head + queued) % slots.size()] = std::move(event);
        ++queued;
    }
public:
    explicit ValidationEventJournal(size_t events=128, size_t bytes=64*1024*1024)
        : max_events(events), max_bytes(bytes), slots(CheckedEvents(events)) {
        if (!events || events > 4096 || bytes < sizeof(Event) ||
            bytes > 64*1024*1024)
            throw std::invalid_argument("Invalid native observer journal budget");
    }
    // All producer operations are noexcept. Allocation/capacity failure makes
    // this journal terminal; it MUST NOT reject or abort a valid native tx.
    void Added(const CTransactionRef &tx, const CCoinsViewCache &view,
               int64_t time) noexcept {
        std::lock_guard<std::mutex> lock(mu);
        if (closed || failure != Failure::None) return;
        try {
            if (!tx || tx->vin.empty() || tx->vout.empty() || tx->IsCoinBase() ||
                tx->vin.size() > 4096 || tx->vout.size() > 4096) {
                Fail(Failure::InvalidPayload); return;
            }
            size_t charge = sizeof(Event) + sizeof(Charge) + 256 +
                sizeof(CTransaction) + RecursiveDynamicUsage(*tx) +
                tx->vin.size()*sizeof(Coin);
            // The acceptance view has already cached every input and its
            // backend is detached. No chainstate lookup or disk read here.
            for (const auto &input : tx->vin) {
                const auto &coin = view.AccessCoin(input.prevout);
                if (coin.IsSpent()) { Fail(Failure::MissingCoin); return; }
                const auto extra = coin.DynamicMemoryUsage();
                if (extra > max_bytes || charge > max_bytes-extra) {
                    Fail(Failure::Bytes); return;
                }
                charge += extra;
            }
            if (!Reserve(charge)) return;
            auto token = std::make_shared<Charge>(budget, charge);
            auto event = std::make_shared<Event>(std::move(token), Kind::Added);
            event->tx = tx; event->accept_time = time;
            event->spent_coins.reserve(tx->vin.size());
            for (const auto &input : tx->vin)
                event->spent_coins.push_back(view.AccessCoin(input.prevout));
            Commit(std::move(event));
        } catch (...) { Fail(Failure::Allocation); }
    }
    void Removed(const CTransactionRef &tx) noexcept {
        std::lock_guard<std::mutex> lock(mu);
        if (closed || failure != Failure::None) return;
        try {
            if (!tx || tx->vin.empty() || tx->vout.empty() || tx->IsCoinBase() ||
                tx->vin.size() > 4096 || tx->vout.size() > 4096) {
                Fail(Failure::InvalidPayload); return;
            }
            const size_t charge = sizeof(Event)+sizeof(Charge)+256+
                sizeof(CTransaction)+RecursiveDynamicUsage(*tx);
            if (!Reserve(charge)) return;
            auto token=std::make_shared<Charge>(budget,charge);
            auto event=std::make_shared<Event>(std::move(token),Kind::Removed);
            event->tx=tx;
            Commit(std::move(event));
        } catch (...) { Fail(Failure::Allocation); }
    }
    void Block(Kind kind, const CBlockIndex *index,
               const std::vector<CTransactionRef> &conflicts={}) noexcept {
        std::lock_guard<std::mutex> lock(mu);
        if (closed || failure != Failure::None) return;
        try {
            if (!index || index->nHeight < 0 || conflicts.size()>4096 ||
                (kind!=Kind::Connected && kind!=Kind::Disconnected)) {
                Fail(Failure::InvalidPayload); return;
            }
            const size_t charge=sizeof(Event)+sizeof(Charge)+256+
                conflicts.size()*sizeof(TxId);
            if (!Reserve(charge)) return;
            auto token=std::make_shared<Charge>(budget,charge);
            auto event=std::make_shared<Event>(std::move(token),kind);
            event->header=index->GetBlockHeader();
            event->height=index->nHeight;event->file=index->nFile;
            event->data_pos=index->nDataPos;event->undo_pos=index->nUndoPos;
            event->status=index->nStatus;
            event->conflicts.reserve(conflicts.size());
            for (const auto &tx : conflicts) {
                if (!tx) { Fail(Failure::InvalidPayload); return; }
                event->conflicts.push_back(tx->GetId());
            }
            Commit(std::move(event));
        } catch (...) { Fail(Failure::Allocation); }
    }
    std::shared_ptr<const Event> Take() {
        std::lock_guard<std::mutex> lock(mu);
        // Never continue after an unrecorded event. Preserve queued payloads
        // for diagnosis; a failed journal cannot be reset or reopened.
        if (!queued || failure!=Failure::None || closed) return {};
        auto event=std::move(slots[head]);
        head=(head+1)%slots.size();--queued;
        return event;
    }
    Status GetStatus() const {
        std::lock_guard<std::mutex> lock(mu);
        return {accepted,queued,budget->events.load(),budget->bytes.load(),
                failure,closed};
    }
    void Close() noexcept {
        std::lock_guard<std::mutex> lock(mu);
        closed=true;
        // Payload destruction is not performed under native locks. Existing
        // leases remain valid/charged; the owner discards after detaching.
    }
};
#endif
