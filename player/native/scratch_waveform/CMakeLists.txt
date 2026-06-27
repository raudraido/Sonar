cmake_minimum_required(VERSION 3.16)
project(scratch_waveform_plugin LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

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
