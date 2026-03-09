cmake_minimum_required(VERSION 3.24)

project(stitch_runtime LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

option(NATIVE_RUNTIME_ENABLE_WARNINGS "Enable extra warnings" ON)

set(HOGAK_LOCAL_OPENCV_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/../.third_party/opencv-python/_skbuild/win-amd64-3.12/cmake-install/x64/vc17/staticlib"
)
if(NOT OpenCV_DIR AND EXISTS "${HOGAK_LOCAL_OPENCV_DIR}/OpenCVConfig.cmake")
    set(OpenCV_DIR "${HOGAK_LOCAL_OPENCV_DIR}" CACHE PATH "Local OpenCV config path")
endif()

find_package(OpenCV CONFIG REQUIRED COMPONENTS core imgproc calib3d cudaarithm cudaimgproc cudawarping)

add_library(
    stitch_engine_core
    STATIC
    src/engine/stitch_engine.cpp
    src/input/ffmpeg_rtsp_reader.cpp
    src/platform/win_process_pipe.cpp
)

target_include_directories(
    stitch_engine_core
    PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${OpenCV_INCLUDE_DIRS}
    PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(MSVC AND NATIVE_RUNTIME_ENABLE_WARNINGS)
    target_compile_options(stitch_engine_core PRIVATE /W4 /permissive-)
elseif(NATIVE_RUNTIME_ENABLE_WARNINGS)
    target_compile_options(stitch_engine_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(
    stitch_runtime
    src/app/runtime_main.cpp
    src/control/control_server.cpp
)

target_include_directories(
    stitch_runtime
    PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(
    stitch_engine_core
    PUBLIC
    ${OpenCV_LIBS}
)

target_link_libraries(
    stitch_runtime
    PRIVATE
    stitch_engine_core
)

if(MSVC AND NATIVE_RUNTIME_ENABLE_WARNINGS)
    target_compile_options(stitch_runtime PRIVATE /W4 /permissive-)
elseif(NATIVE_RUNTIME_ENABLE_WARNINGS)
    target_compile_options(stitch_runtime PRIVATE -Wall -Wextra -Wpedantic)
endif()
