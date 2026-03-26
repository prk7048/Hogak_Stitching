cmake_minimum_required(VERSION 3.24)

project(stitch_runtime LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

option(NATIVE_RUNTIME_ENABLE_WARNINGS "Enable extra warnings" ON)
option(HOGAK_ENABLE_GPU_DIRECT_AVCODEC "Enable libavcodec/NVENC gpu-direct writer path" ON)

set(HOGAK_REPO_LOCAL_OPENCV_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/../.third_party/opencv-python/_skbuild/win-amd64-3.12/cmake-install/x64/vc17/staticlib"
)
set(HOGAK_LOCAL_OPENCV_DIR "" CACHE PATH "Optional repo-local OpenCV config directory override")

set(_hogak_opencv_candidates "")
if(OpenCV_DIR)
    list(APPEND _hogak_opencv_candidates "${OpenCV_DIR}")
endif()
if(DEFINED ENV{OpenCV_DIR} AND NOT "$ENV{OpenCV_DIR}" STREQUAL "")
    list(APPEND _hogak_opencv_candidates "$ENV{OpenCV_DIR}")
endif()
if(DEFINED ENV{OPENCV_DIR} AND NOT "$ENV{OPENCV_DIR}" STREQUAL "")
    list(APPEND _hogak_opencv_candidates "$ENV{OPENCV_DIR}")
endif()
if(HOGAK_LOCAL_OPENCV_DIR)
    list(APPEND _hogak_opencv_candidates "${HOGAK_LOCAL_OPENCV_DIR}")
endif()
list(APPEND _hogak_opencv_candidates "${HOGAK_REPO_LOCAL_OPENCV_DIR}")

set(_hogak_opencv_resolved "")
foreach(_hogak_candidate IN LISTS _hogak_opencv_candidates)
    if(NOT _hogak_candidate)
        continue()
    endif()
    if(EXISTS "${_hogak_candidate}/OpenCVConfig.cmake")
        set(_hogak_opencv_resolved "${_hogak_candidate}")
        break()
    endif()
endforeach()

if(NOT OpenCV_DIR AND _hogak_opencv_resolved)
    set(OpenCV_DIR "${_hogak_opencv_resolved}" CACHE PATH "Resolved OpenCV config path" FORCE)
endif()

set(HOGAK_FFMPEG_DEV_ROOT "" CACHE PATH "FFmpeg development root containing include/ and lib/ for future gpu-direct writer work")

set(HOGAK_GPU_DIRECT_PROVIDER "ffmpeg-libav-nvenc")
set(HOGAK_GPU_DIRECT_AVCODEC_ENABLED OFF)
set(HOGAK_GPU_DIRECT_DEPENDENCY_STATUS "disabled")
set(HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT "")
set(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED OFF)

find_package(CUDAToolkit QUIET)
if(CUDAToolkit_FOUND)
    set(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED ON)
endif()

set(_hogak_ffmpeg_dev_candidates "")
if(HOGAK_FFMPEG_DEV_ROOT)
    list(APPEND _hogak_ffmpeg_dev_candidates "${HOGAK_FFMPEG_DEV_ROOT}")
endif()
if(DEFINED ENV{HOGAK_FFMPEG_DEV_ROOT} AND NOT "$ENV{HOGAK_FFMPEG_DEV_ROOT}" STREQUAL "")
    list(APPEND _hogak_ffmpeg_dev_candidates "$ENV{HOGAK_FFMPEG_DEV_ROOT}")
endif()
if(DEFINED ENV{FFMPEG_DEV_ROOT} AND NOT "$ENV{FFMPEG_DEV_ROOT}" STREQUAL "")
    list(APPEND _hogak_ffmpeg_dev_candidates "$ENV{FFMPEG_DEV_ROOT}")
endif()
list(APPEND _hogak_ffmpeg_dev_candidates
    "${CMAKE_CURRENT_SOURCE_DIR}/../.third_party/ffmpeg-dev/current"
    "${CMAKE_CURRENT_SOURCE_DIR}/../.third_party/ffmpeg/current"
)

set(_hogak_ffmpeg_dev_include "")
set(_hogak_ffmpeg_dev_lib "")
foreach(_hogak_candidate IN LISTS _hogak_ffmpeg_dev_candidates)
    if(NOT _hogak_candidate)
        continue()
    endif()
    if(EXISTS "${_hogak_candidate}/include/libavcodec/avcodec.h"
       AND EXISTS "${_hogak_candidate}/include/libavformat/avformat.h"
       AND EXISTS "${_hogak_candidate}/include/libavutil/hwcontext.h"
       AND EXISTS "${_hogak_candidate}/include/libswscale/swscale.h"
       AND EXISTS "${_hogak_candidate}/lib/avcodec.lib"
       AND EXISTS "${_hogak_candidate}/lib/avformat.lib"
       AND EXISTS "${_hogak_candidate}/lib/avutil.lib"
       AND EXISTS "${_hogak_candidate}/lib/swscale.lib")
        set(_hogak_ffmpeg_dev_include "${_hogak_candidate}/include")
        set(_hogak_ffmpeg_dev_lib "${_hogak_candidate}/lib")
        set(HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT "${_hogak_candidate}")
        break()
    endif()
endforeach()

if(NOT _hogak_ffmpeg_dev_include OR NOT _hogak_ffmpeg_dev_lib)
    message(FATAL_ERROR
        "FFmpeg development headers/libs are required for the native RTSP reader. "
        "Set HOGAK_FFMPEG_DEV_ROOT to a directory containing include/libavcodec and lib/avcodec.lib.")
endif()

if(HOGAK_ENABLE_GPU_DIRECT_AVCODEC)
    set(HOGAK_GPU_DIRECT_AVCODEC_ENABLED ON)
    set(HOGAK_GPU_DIRECT_DEPENDENCY_STATUS
        "ready: FFmpeg dev headers/libs found for libav input reader and gpu-direct writer")
else()
    set(HOGAK_GPU_DIRECT_DEPENDENCY_STATUS
        "ready: FFmpeg dev headers/libs found for libav input reader; gpu-direct writer disabled by HOGAK_ENABLE_GPU_DIRECT_AVCODEC=OFF")
endif()

message(STATUS "Hogak OpenCV_DIR=${OpenCV_DIR}")
message(STATUS "Hogak FFmpeg dev root=${HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT}")
message(STATUS "Hogak gpu-direct status=${HOGAK_GPU_DIRECT_DEPENDENCY_STATUS}")

set(HOGAK_GPU_DIRECT_AVCODEC_ENABLED_INT 0)
if(HOGAK_GPU_DIRECT_AVCODEC_ENABLED)
    set(HOGAK_GPU_DIRECT_AVCODEC_ENABLED_INT 1)
endif()

set(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED_INT 0)
if(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED)
    set(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED_INT 1)
endif()

file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated/output")
file(TO_CMAKE_PATH "${HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT}" HOGAK_GPU_DIRECT_FFMPEG_DEV_ROOT_CMAKE)
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/src/output/gpu_direct_build_config.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/output/gpu_direct_build_config.h"
    @ONLY
)

find_package(OpenCV CONFIG REQUIRED COMPONENTS core imgproc imgcodecs calib3d cudaarithm cudaimgproc cudawarping)

add_library(
    stitch_engine_core
    STATIC
    src/engine/stitch_engine.cpp
    src/input/ffmpeg_rtsp_reader.cpp
    src/output/ffmpeg_output_writer.cpp
    src/output/gpu_direct_support.cpp
    src/output/gpu_direct_output_writer.cpp
    src/output/output_writer_factory.cpp
    src/platform/win_process_pipe.cpp
    src/platform/win_process_sink.cpp
)

target_include_directories(
    stitch_engine_core
    PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${OpenCV_INCLUDE_DIRS}
    ${CMAKE_CURRENT_BINARY_DIR}/generated
    PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${_hogak_ffmpeg_dev_include}
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
    PRIVATE
    "${_hogak_ffmpeg_dev_lib}/avcodec.lib"
    "${_hogak_ffmpeg_dev_lib}/avformat.lib"
    "${_hogak_ffmpeg_dev_lib}/avutil.lib"
    "${_hogak_ffmpeg_dev_lib}/swscale.lib"
)

if(HOGAK_GPU_DIRECT_CUDA_RUNTIME_ENABLED)
    target_link_libraries(
        stitch_engine_core
        PRIVATE
        CUDA::cudart
    )
endif()

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
