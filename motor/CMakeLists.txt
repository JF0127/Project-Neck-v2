cmake_minimum_required(VERSION 3.10)
project(MasterStack)

set(BUILD_EXAMPLES OFF CACHE BOOL "Build cpp-readline examples" FORCE)
add_subdirectory(thirdparty)

find_package(Boost REQUIRED)

include_directories(${Boost_INCLUDE_DIR})
include_directories(
        thirdparty/soem/soem
        thirdparty/soem/osal
        thirdparty/soem/osal/linux
        thirdparty/soem/oshw/linux
        thirdparty/cpp-readline/src
)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)

set(CMAKE_CXX_STANDARD 17)

set(SOURCES
    src/protocol/motor_control.c
    src/protocol/math_ops.c
    src/ethercat/transmit.cpp
    src/neck/neck_sweep.cpp
    src/neck/controller.cpp
    src/neck/neck_kinematics.cpp
    src/neck/neck_stream.cpp
)
LINK_LIBRARIES(m)
add_library(motor ${SOURCES})
target_include_directories(motor PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(motor soem)
target_compile_definitions(motor PRIVATE
    NECK_RUNTIME_PATH="${CMAKE_CURRENT_SOURCE_DIR}/config/runtime.json"
    NECK_SWEEP_DATA_DIR="${CMAKE_CURRENT_SOURCE_DIR}/data/sweep"
    NECK_CALIBRATION_PATH="${CMAKE_CURRENT_SOURCE_DIR}/config/neck_calibration.json"
    NECK_TRAJECTORY_DIR="/home/jhl/projects/Project-Neck-v2/motor/data/demos"
)

add_executable(master_stack_test
    src/console/main.cpp
    src/console/command.cpp
    src/console/neck_commands.cpp
)
target_link_libraries(master_stack_test motor soem cpp-readline)