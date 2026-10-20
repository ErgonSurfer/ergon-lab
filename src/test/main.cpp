// Copyright (c) 2011-2016 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#define BOOST_TEST_MODULE Bitcoin Static unit tests

#include <util/system.h>

#include <boost/test/unit_test.hpp>
#include <boost/test/tree/global_fixture.hpp>
#include <iostream>

namespace utf = boost::unit_test::framework;

namespace {
// Stable, flushed case identities in every native test profile, including
// compiled-out. Keep one observer here rather than in a Chronik-only unit:
// missing/duplicate starts or finishes must still fail the public runner.
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
} // namespace

/*
 * Global fixture for passing custom arguments, and clearing them all after each
 * test case.
 */
struct CustomArgumentsFixture {
    std::string error;

    CustomArgumentsFixture() {
        const std::string testsuitename = "-testsuitename";

        const std::set<std::string> testArgs = {
            testsuitename,
            "-axionactivationtime",
            "-tachyonactivationtime",
#ifdef CHRONIK_ERGON_PLUGIN_TESTS
            // Public test driver only; never registered by the daemon.
            "-ergonpluginrestartroot",
#endif
        };

        for (const auto &arg : testArgs) {
            gArgs.AddArg(arg, "", false, OptionsCategory::HIDDEN);
        }

        auto &master_test_suite = utf::master_test_suite();
        if (!gArgs.ParseParameters(master_test_suite.argc,
                                   master_test_suite.argv, error)) {
            throw utf::setup_error(error);
        }

        master_test_suite.p_name.value =
            gArgs.GetArg(testsuitename, master_test_suite.p_name.value);
    }

    ~CustomArgumentsFixture(){};
};

BOOST_GLOBAL_FIXTURE(CustomArgumentsFixture);
