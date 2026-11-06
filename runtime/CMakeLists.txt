# Project Swallow PS1 runtime core. C11; GCC, Clang, and MSVC.
# Contract: docs/recompiler.md. Built on its own, it also builds the unit
# tests and the recompiler self-test; added from a generated project, it
# provides only the core library.
cmake_minimum_required(VERSION 3.21)
project(swallow_runtime LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# Warnings are errors for the runtime's own sources, never for generated code.
function(swallow_strict_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /WX)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Werror)
  endif()
endfunction()

add_library(swallow_ps1_core STATIC ps1/core/cpu.c ps1/core/guest.c)
target_include_directories(swallow_ps1_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(swallow_ps1_core PROPERTIES
  POSITION_INDEPENDENT_CODE ON
  C_VISIBILITY_PRESET hidden)
swallow_strict_warnings(swallow_ps1_core)

if(PROJECT_IS_TOP_LEVEL)
  enable_testing()

  add_executable(swallow_core_tests tests/test_core.c)
  target_link_libraries(swallow_core_tests PRIVATE swallow_ps1_core)
  swallow_strict_warnings(swallow_core_tests)
  add_test(NAME core COMMAND swallow_core_tests)

  # The self-test translates synthetic programs with swallow-recompile and
  # compares the native results with the Python interpreter's.
  find_package(Python3 3.11 COMPONENTS Interpreter)
  if(Python3_FOUND)
    set(SWALLOW_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../src)
    set(SELFTEST_DIR ${CMAKE_CURRENT_BINARY_DIR}/selftest)
    set(SELFTEST_FILES ${SELFTEST_DIR}/selftest_module.c ${SELFTEST_DIR}/selftest_main.c)
    file(GLOB SWALLOW_PY ${SWALLOW_SRC}/swallow_tools/*.py)
    add_custom_command(
      OUTPUT ${SELFTEST_FILES}
      COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${SWALLOW_SRC}
              ${Python3_EXECUTABLE} -m swallow_tools.recompile --selftest-out ${SELFTEST_DIR}
      DEPENDS ${SWALLOW_PY}
      COMMENT "Generating the recompiler self-test")
    add_executable(swallow_selftest ${SELFTEST_FILES})
    target_link_libraries(swallow_selftest PRIVATE swallow_ps1_core)
    add_test(NAME selftest COMMAND swallow_selftest)

    # The same translated programs as a shared library, run through ctypes
    # by the Python side, as swallow-ulzcheck --native does.
    add_library(swallow_selftest_module SHARED ${SELFTEST_DIR}/selftest_module.c)
    target_link_libraries(swallow_selftest_module PRIVATE swallow_ps1_core)
    set_target_properties(swallow_selftest_module PROPERTIES C_VISIBILITY_PRESET hidden)
    add_test(NAME native_selftest
             COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${SWALLOW_SRC}
                     ${Python3_EXECUTABLE} -m swallow_tools.native
                     $<TARGET_FILE:swallow_selftest_module>)
  else()
    message(STATUS "Python 3.11 or later not found; skipping the recompiler self-test")
  endif()
endif()
