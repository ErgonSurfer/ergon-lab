// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_JOURNAL_REPLAY_H
#define ERGON_JOURNAL_REPLAY_H
#include <chronik-cpp/ergon_node_access.h>
#include <validation_event_journal.h>
#include <memory>
#include <exception>
namespace ergon_archival {
struct ArchivalProbe;
// Only explicit native shutdown may take this non-corruption exit. Storage,
// journal continuity and database errors retain their fail-closed paths.
class JournalReplayCancelled final : public std::exception {
public:
    const char *what() const noexcept override {
        return "Native shutdown cancelled journal replay";
    }
};
// Synchronous owned consumer, optionally owned by the continuous worker.
// The probe and native access MUST outlive this object. Calls own no native
// locks on entry. Stop closes admission before waiting for an active consumer.
class JournalReplay {
    struct Impl;
    std::unique_ptr<Impl> impl;
    explicit JournalReplay(std::unique_ptr<Impl>);
public:
    JournalReplay(ArchivalProbe &,const chronik_bridge::ErgonNodeAccess &,
                  size_t events=128,size_t bytes=64*1024*1024);
    // Only at the successful initial snapshot publication fence; no DB work.
    static std::unique_ptr<JournalReplay> AnchorUnderNativeFence(
        ArchivalProbe &,const chronik_bridge::ErgonNodeAccess &,
        size_t events=128,size_t bytes=64*1024*1024);
    ~JournalReplay();
    JournalReplay(const JournalReplay &)=delete;
    JournalReplay &operator=(const JournalReplay &)=delete;
    uint64_t Drain();
    bool Publish();
    ValidationEventJournal::Status Status() const;
    void Stop();
};
}
#endif
