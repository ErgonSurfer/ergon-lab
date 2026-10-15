# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
# Ordinary link builds are dormant. A separate default-OFF regtest profile owns
# the bounded experimental runtime; this is not general daemon admission.

if(CMAKE_VERSION VERSION_LESS "3.20")
    message(FATAL_ERROR "Upstream Chronik link integration requires CMake 3.20+")
endif()
if(WIN32)
    message(FATAL_ERROR "The upstream link profile is not yet validated on Windows")
endif()
if(BUILD_CHRONIK_UPSTREAM_REGTEST AND BUILD_BITCOIN_QT)
    message(FATAL_ERROR "Experimental regtest service requires Qt OFF")
endif()
if(BUILD_CHRONIK_OBSERVER)
    message(FATAL_ERROR "Upstream link and the older observer are separate profiles")
endif()

find_program(ERGON_UPSTREAM_CARGO cargo REQUIRED)
find_package(Threads REQUIRED)
if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    message(FATAL_ERROR "Set an explicit shared CMAKE_OSX_DEPLOYMENT_TARGET for native and Rust code")
endif()
set(CHRONIK_UPSTREAM_CARGO_HOME "" CACHE PATH "Reviewed offline Cargo cache")
set(CHRONIK_UPSTREAM_TARGET_DIR "${CMAKE_BINARY_DIR}/cargo/upstream" CACHE PATH
    "Generated upstream Rust and CXX outputs; keep outside the source tree")
set(CHRONIK_UPSTREAM_LIBCLANG_DIR "" CACHE PATH "libclang directory for RocksDB bindings")
file(REAL_PATH "${CMAKE_SOURCE_DIR}" _upstream_source)
file(REAL_PATH "${CHRONIK_UPSTREAM_TARGET_DIR}" _upstream_target)
string(FIND "${_upstream_target}/" "${_upstream_source}/" _upstream_inside)
if(_upstream_inside EQUAL 0)
    message(FATAL_ERROR "Generated upstream outputs must not enter the source tree")
endif()
set(_upstream_env
    "CARGO_TARGET_DIR=${CHRONIK_UPSTREAM_TARGET_DIR}"
    "CARGO_PROFILE_DEV_DEBUG=0" "CARGO_INCREMENTAL=0")
if(APPLE)
    list(APPEND _upstream_env "MACOSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}")
endif()
if(CHRONIK_UPSTREAM_CARGO_HOME)
    list(APPEND _upstream_env "CARGO_HOME=${CHRONIK_UPSTREAM_CARGO_HOME}")
endif()
if(CHRONIK_UPSTREAM_LIBCLANG_DIR)
    list(APPEND _upstream_env "LIBCLANG_PATH=${CHRONIK_UPSTREAM_LIBCLANG_DIR}")
endif()
set(_upstream_archive "${CHRONIK_UPSTREAM_TARGET_DIR}/debug/libchronik_lib.a")
set(_upstream_generated
    "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge/chronik-bridge/src/ffi.rs.cc"
    "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge/chronik_lib/src/ffi.rs.cc")
set(_upstream_features "")
set(_upstream_probe_sources "")
if(BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS)
    find_package(Python3 3.12 EXACT COMPONENTS Interpreter Development.Embed REQUIRED)
    list(APPEND _upstream_env "PYO3_PYTHON=${Python3_EXECUTABLE}"
        "PYTHONDONTWRITEBYTECODE=1"
        "CHRONIK_ERGON_PYTHON_VERSION=${Python3_VERSION}")
    file(SHA256 "${Python3_EXECUTABLE}" _python_executable_sha256)
    file(SHA256 "${Python3_LIBRARIES}" _python_library_sha256)
    string(SHA256 _python_identity
        "${Python3_VERSION}\n${_python_executable_sha256}\n${_python_library_sha256}\n")
    list(APPEND _upstream_env "CHRONIK_ERGON_PYTHON_ID=${_python_identity}")
    list(APPEND _upstream_features --features plugins)
endif()
if(BUILD_CHRONIK_UPSTREAM_ARCHIVAL_TESTS)
    # Bind the authored adapter and dependency graph, never an input path.
    set(_identity_inputs
        chronik/Cargo.lock
        chronik/chronik-bridge/src/ffi.rs
        chronik/chronik-cpp/chronik_bridge.cpp
        chronik/chronik-cpp/chronik_bridge.h
        chronik/chronik-cpp/ergon_position_reader.cpp
        chronik/chronik-cpp/ergon_position_reader.h
        chronik/chronik-cpp/ergon_archival_probe.cpp
        chronik/chronik-cpp/ergon_archival_probe.h
        chronik/chronik-cpp/ergon_archival_worker.cpp
        chronik/chronik-cpp/ergon_archival_worker.h
        chronik/chronik-cpp/ergon_node_access.h
        chronik/chronik-lib/Cargo.toml
        chronik/chronik-lib/build.rs
        chronik/chronik-lib/src/ergon_archival.rs)
    list(APPEND _identity_inputs chronik/chronik-lib/src/ergon_runtime.rs
        chronik/chronik-lib/src/bridge.rs chronik/chronik-lib/src/lib.rs
        chronik/chronik-lib/src/ergon_service.rs chronik/chronik-http/src/server.rs)
    list(APPEND _identity_inputs src/chain.h src/chain.cpp src/txmempool.h
        src/txmempool.cpp src/util/source_generation.h)
    list(APPEND _identity_inputs chronik/chronik-lib/src/ergon_http_tests.rs)
    list(APPEND _identity_inputs src/coins.h src/coins.cpp src/dbwrapper.h
        src/dbwrapper.cpp src/txdb.h src/txdb.cpp)
    list(APPEND _identity_inputs chronik/chronik-lib/src/ergon_plugin_fixture.rs
        chronik/chronik-db/src/mem/mempool.rs
        tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py
        src/test/main.cpp)
    list(APPEND _identity_inputs CMakeLists.txt src/init.cpp
        chronik/chronik-cpp/ergon_regtest_service.h
        chronik/chronik-cpp/ergon_regtest_service.cpp)
    if(BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS)
        list(APPEND _identity_inputs chronik/chronik-plugin-impl/src/module.rs
            chronik/chronik-plugin-impl/python/upstream/script.py
            chronik/chronik-plugin-impl/python/upstream/slp.py)
    endif()
    set(_identity "ergon-archival-adapter/v1\n")
    foreach(_input IN LISTS _identity_inputs)
        file(SHA256 "${CMAKE_SOURCE_DIR}/${_input}" _digest)
        string(APPEND _identity "${_input}:${_digest}\n")
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
            "${CMAKE_SOURCE_DIR}/${_input}")
    endforeach()
    if(BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS)
        string(APPEND _identity "python-profile:${_python_identity}\n")
    endif()
    string(SHA256 _adapter_id "${_identity}")
    list(APPEND _upstream_env "CHRONIK_ERGON_ADAPTER_ID=${_adapter_id}")
    list(APPEND _upstream_features --features ergon-archival-probe)
    list(APPEND _upstream_generated
        "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge/chronik_lib/src/ergon_archival.rs.cc")
    list(APPEND _upstream_probe_sources
        "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/ergon_archival_probe.cpp"
        "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/ergon_archival_worker.cpp")
endif()
add_custom_target(chronik_upstream_rust_build
    COMMAND "${CMAKE_COMMAND}" -E env ${_upstream_env}
        "${ERGON_UPSTREAM_CARGO}" build
        --manifest-path "${CMAKE_SOURCE_DIR}/chronik/Cargo.toml"
        --package chronik_lib --no-default-features ${_upstream_features} --locked --offline -j2
    BYPRODUCTS "${_upstream_archive}" ${_upstream_generated}
    COMMENT "Building the real upstream indexer and server (runtime opt-in separately governed)"
    USES_TERMINAL VERBATIM)
set_source_files_properties(${_upstream_generated} PROPERTIES GENERATED TRUE)
add_library(chronik_upstream_native OBJECT
    "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/chronik_bridge.cpp"
    "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/ergon_position_reader.cpp"
    "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/chronik_validationinterface.cpp"
    ${_upstream_probe_sources}
    ${_upstream_generated})
add_dependencies(chronik_upstream_native chronik_upstream_rust_build)
target_include_directories(chronik_upstream_native PRIVATE
    "${CMAKE_SOURCE_DIR}/chronik" "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge")
target_link_libraries(chronik_upstream_native PRIVATE server Threads::Threads)
function(ergon_link_upstream_chronik target)
target_sources(${target} PRIVATE $<TARGET_OBJECTS:chronik_upstream_native>)
add_dependencies(${target} chronik_upstream_rust_build)
# A force-load linker option alone is not a Ninja file dependency. Relink when
# Rust implementation bytes change but generated CXX signatures do not.
set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS "${_upstream_archive}")
# Do not let archive dead stripping produce a false-positive link with no
# actual indexer/server pulled in. No native unresolved-symbol exemption.
if(APPLE)
    target_link_options(${target} PRIVATE "-Wl,-force_load,${_upstream_archive}")
    target_link_libraries(${target} iconv)
else()
    target_link_libraries(${target}
        "-Wl,--whole-archive" "${_upstream_archive}" "-Wl,--no-whole-archive")
    target_link_libraries(${target} ${CMAKE_DL_LIBS} m)
endif()
target_link_libraries(${target} server Threads::Threads)
if(BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS)
    target_link_libraries(${target} Python3::Python)
    target_compile_definitions(${target} PRIVATE CHRONIK_ERGON_PLUGIN_TESTS=1)
endif()
endfunction()

ergon_link_upstream_chronik(bitcoind)
if(BUILD_CHRONIK_UPSTREAM_REGTEST)
    target_sources(chronik_upstream_native PRIVATE
        "${CMAKE_SOURCE_DIR}/chronik/chronik-cpp/ergon_regtest_service.cpp")
    target_include_directories(server PRIVATE "${CMAKE_SOURCE_DIR}/chronik")
    target_compile_definitions(server PRIVATE ENABLE_CHRONIK_UPSTREAM_REGTEST=1)
    target_include_directories(bitcoind PRIVATE
        "${CMAKE_SOURCE_DIR}/chronik" "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge")
endif()
if(TARGET test_bitcoin)
    ergon_link_upstream_chronik(test_bitcoin)
    target_sources(test_bitcoin PRIVATE
        "${CMAKE_SOURCE_DIR}/src/test/chronik_native_upstream_bridge_tests.cpp")
    if(BUILD_CHRONIK_UPSTREAM_ARCHIVAL_TESTS)
        target_sources(test_bitcoin PRIVATE
            "${CMAKE_SOURCE_DIR}/src/test/chronik_upstream_archival_tests.cpp")
    endif()
    if(BUILD_CHRONIK_UPSTREAM_REGTEST)
        target_sources(test_bitcoin PRIVATE
            "${CMAKE_SOURCE_DIR}/src/test/chronik_upstream_regtest_tests.cpp")
    endif()
    target_include_directories(test_bitcoin PRIVATE
        "${CMAKE_SOURCE_DIR}/chronik" "${CHRONIK_UPSTREAM_TARGET_DIR}/cxxbridge")
endif()
