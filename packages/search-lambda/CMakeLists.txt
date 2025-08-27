cmake_minimum_required(VERSION 3.16)
project(fdnix-search-lambda)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Set build type to Release if not specified
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Compiler flags for optimization and security
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG -flto")
set(CMAKE_CXX_FLAGS_DEBUG "-g -O0")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra -fPIC")

# Find required packages
find_package(CURL REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(ZLIB REQUIRED)

# Find AWS SDK for C++ (for HTTP client utilities)
find_package(AWSSDK REQUIRED COMPONENTS core bedrock-runtime)

# Find AWS Lambda Runtime
find_package(aws-lambda-runtime REQUIRED)

# Find DuckDB static library - prioritize static linking
find_library(DUCKDB_LIBRARY
    NAMES libduckdb.a duckdb_static libduckdb_static libduckdb
    PATHS /usr/local/lib /opt/lib
    REQUIRED
)
find_path(DUCKDB_INCLUDE_DIR
    NAMES duckdb.hpp
    PATHS /usr/local/include /opt/include
    REQUIRED
)

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
include_directories(${DUCKDB_INCLUDE_DIR})

# Source files
set(SOURCES
    src/main.cpp
    src/duckdb_client.cpp
    src/bedrock_client.cpp
)

# Create the Lambda executable
add_executable(${PROJECT_NAME} ${SOURCES})

# Set the output name to 'bootstrap' for Lambda custom runtime
set_target_properties(${PROJECT_NAME} PROPERTIES OUTPUT_NAME bootstrap)

# Link libraries statically where possible
target_link_libraries(${PROJECT_NAME}
    AWS::aws-lambda-runtime
    ${AWSSDK_LINK_LIBRARIES}
    ${CURL_LIBRARIES}
    ZLIB::ZLIB
    ${DUCKDB_LIBRARY}
    -static-libgcc
    -static-libstdc++
    dl  # For dynamic loading
    pthread  # Required for threading
)

# Install the bootstrap binary
install(TARGETS ${PROJECT_NAME} DESTINATION bin)

# Copy the bootstrap binary to dist directory for CDK deployment
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_CURRENT_SOURCE_DIR}/dist
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${PROJECT_NAME}> ${CMAKE_CURRENT_SOURCE_DIR}/dist/bootstrap
    COMMENT "Copying bootstrap to dist directory"
)
