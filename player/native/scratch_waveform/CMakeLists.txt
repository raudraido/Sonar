cmake_minimum_required(VERSION 3.16)
project(scratch_waveform_plugin LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# Force the plugin DLL (.dll/.so) into the same flat directory as qmldir
# regardless of config, on both single- and multi-config generators. On the
# default VS generator on Windows (needed to match PyQt6's MSVC-built Qt6 —
# see build.py's _find_qt6_cmake_prefix), qt_add_qml_module's plugin module
# target otherwise lands at build/<name>.dll (RUNTIME_OUTPUT_DIRECTORY is
# left unset by the macro for the module target) instead of directly under
# OUTPUT_DIRECTORY/, where qmldir's "plugin" entry expects to find it next
# to itself — breaking the runtime import silently.
set(_plugin_out_dir ${CMAKE_BINARY_DIR}/qml/FooterNativeWaveform)
foreach(_cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY${_cfg} ${_plugin_out_dir})
    set(CMAKE_LIBRARY_OUTPUT_DIRECTORY${_cfg} ${_plugin_out_dir})
    set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY${_cfg} ${_plugin_out_dir})
endforeach()

find_package(Qt6 6.2 REQUIRED COMPONENTS Core Gui Qml Quick)

qt_add_qml_module(scratch_waveform_plugin
    URI FooterNativeWaveform
    VERSION 1.0
    OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/qml/FooterNativeWaveform
    SOURCES
        scratchwaveformitem.h
        scratchwaveformitem.cpp
)

target_link_libraries(scratch_waveform_plugin PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
)
