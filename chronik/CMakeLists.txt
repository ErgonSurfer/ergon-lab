# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers

include(DoOrFail)
find_program_or_fail(CHRONIK_CARGO_EXECUTABLE cargo)
find_program_or_fail(CHRONIK_RUSTC_EXECUTABLE rustc)

execute_process(
    COMMAND "${CHRONIK_RUSTC_EXECUTABLE}" --version
    RESULT_VARIABLE CHRONIK_RUSTC_RESULT
    OUTPUT_VARIABLE CHRONIK_RUSTC_OUTPUT
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT CHRONIK_RUSTC_RESULT EQUAL 0)
    message(FATAL_ERROR "Unable to query the Rust compiler")
endif()
string(REGEX MATCH "rustc ([0-9]+[.][0-9]+[.][0-9]+)" _ "${CHRONIK_RUSTC_OUTPUT}")
set(CHRONIK_RUSTC_VERSION "${CMAKE_MATCH_1}")
if(NOT CHRONIK_RUSTC_VERSION OR CHRONIK_RUSTC_VERSION VERSION_LESS "1.80.0")
    message(FATAL_ERROR
        "Chronik build-only requires Rust 1.80.0 or newer; found ${CHRONIK_RUSTC_OUTPUT}"
    )
endif()

set(CHRONIK_BUILD_ONLY_TARGET_DIR
    "${CMAKE_BINARY_DIR}/cargo/chronik-build-only"
)
set(CHRONIK_CARGO_HOME "" CACHE PATH
    "Optional isolated Cargo home for the Chronik build-only target"
)
set(CHRONIK_CARGO_ENV
    "CARGO_TARGET_DIR=${CHRONIK_BUILD_ONLY_TARGET_DIR}"
)
if(CHRONIK_CARGO_HOME)
    list(APPEND CHRONIK_CARGO_ENV "CARGO_HOME=${CHRONIK_CARGO_HOME}")
endif()
add_custom_target(chronik_build_only ALL
    COMMAND
        "${CMAKE_COMMAND}" -E env
        ${CHRONIK_CARGO_ENV}
        "${CHRONIK_CARGO_EXECUTABLE}"
        build
        --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/Cargo.toml"
        --workspace
        --release
        --locked
        --offline
    COMMENT "Building isolated Chronik Rust primitives"
    USES_TERMINAL
    VERBATIM
)

add_custom_target(check-chronik-build-only
    COMMAND
        "${CMAKE_COMMAND}" -E env
        ${CHRONIK_CARGO_ENV}
        "${CHRONIK_CARGO_EXECUTABLE}"
        test
        --manifest-path "${CMAKE_CURRENT_SOURCE_DIR}/Cargo.toml"
        --workspace
        --release
        --locked
        --offline
    COMMENT "Testing isolated Chronik Rust primitives"
    USES_TERMINAL
    VERBATIM
)
add_dependencies(check check-chronik-build-only)
