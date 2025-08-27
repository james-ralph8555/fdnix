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
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")
set(CMAKE_CXX_FLAGS_DEBUG "-g -O0")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra")

# Configure for fully static linking
set(CMAKE_EXE_LINKER_FLAGS "-static-libgcc -static-libstdc++ -Wl,--as-needed")
set(BUILD_SHARED_LIBS OFF)
set(CMAKE_FIND_LIBRARY_SUFFIXES .a)

# Find required packages with modern CMake targets
find_package(AWSSDK REQUIRED COMPONENTS core bedrock-runtime)
find_package(aws-lambda-runtime REQUIRED)
find_package(ZLIB REQUIRED)

# Find DuckDB bundled static library with all dependencies
find_library(DUCKDB_LIBRARY
    NAMES libduckdb_bundle.a
    PATHS /usr/local/lib
    REQUIRED
)
find_path(DUCKDB_INCLUDE_DIR
    NAMES duckdb.hpp
    PATHS /usr/local/include
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

# Add DuckDB static build definition
target_compile_definitions(${PROJECT_NAME} PRIVATE DUCKDB_STATIC_BUILD)

# Link libraries with proper order for static linking
# Higher-level libraries are listed first, followed by their dependencies.
target_link_libraries(${PROJECT_NAME}
    # --- Main Application Libraries ---
    ${DUCKDB_LIBRARY}
    AWS::aws-lambda-runtime
    aws-cpp-sdk-bedrock-runtime
    aws-cpp-sdk-core

    # --- AWS CRT Libraries (Dependencies of AWS SDK) ---
    aws-crt-cpp
    aws-c-http
    aws-c-mqtt
    aws-c-s3
    aws-c-auth
    aws-c-cal
    aws-c-io
    aws-c-compression
    aws-c-sdkutils
    aws-checksums
    aws-c-event-stream
    aws-c-common

    # --- System Libraries (let pkg-config and find_package handle paths) ---
    curl
    ssl
    crypto
    gssapi_krb5
    krb5
    k5crypto
    com_err
    krb5support
    ldap
    lber
    ssh
    nghttp2
    idn2
    psl
    brotlidec
    brotlicommon
    zstd
    rtmp
    z
    
    # --- System Libraries ---
    uuid
    dl
    pthread
    m
    rt
)

message(STATUS "Using DuckDB bundled library: ${DUCKDB_LIBRARY}")

# Install the bootstrap binary
install(TARGETS ${PROJECT_NAME} DESTINATION bin)

# Copy the bootstrap binary to dist directory for CDK deployment
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_CURRENT_SOURCE_DIR}/dist
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${PROJECT_NAME}> ${CMAKE_CURRENT_SOURCE_DIR}/dist/bootstrap
    COMMENT "Copying bootstrap to dist directory"
)
