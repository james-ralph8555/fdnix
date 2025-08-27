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

# Prefer static linking where possible
set(CMAKE_EXE_LINKER_FLAGS "-Wl,--as-needed")
set(BUILD_SHARED_LIBS OFF)

# Find required packages
find_package(CURL REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(ZLIB REQUIRED)

# Find AWS SDK for C++ (for HTTP client utilities)
find_package(AWSSDK REQUIRED COMPONENTS core bedrock-runtime)

# Find AWS Lambda Runtime
find_package(aws-lambda-runtime REQUIRED)

# Find DuckDB library - use shared lib for now, fix runtime later
find_library(DUCKDB_LIBRARY
    NAMES libduckdb.so libduckdb_static.a libduckdb.a duckdb_static libduckdb_static
    PATHS /usr/local/lib64 /usr/local/lib /opt/lib64 /opt/lib
    REQUIRED
)
find_path(DUCKDB_INCLUDE_DIR
    NAMES duckdb.hpp
    PATHS /usr/local/include /opt/include
    REQUIRED
)

# Find DuckDB extension libraries if they exist separately
find_library(DUCKDB_FTS_LIBRARY
    NAMES libduckdb_fts.a duckdb_fts
    PATHS /usr/local/lib /opt/lib
    QUIET
)
find_library(DUCKDB_VSS_LIBRARY
    NAMES libduckdb_vss.a duckdb_vss
    PATHS /usr/local/lib /opt/lib
    QUIET
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

# Set RPATH to look for libraries in lib directory relative to binary
set_target_properties(${PROJECT_NAME} PROPERTIES 
    BUILD_RPATH "/usr/local/lib64"
    INSTALL_RPATH "$ORIGIN/lib:/usr/local/lib64"
    BUILD_WITH_INSTALL_RPATH FALSE
    INSTALL_RPATH_USE_LINK_PATH TRUE
)

# Precompiled DuckDB binary includes all dependencies - no need to search for separate libraries

# Link libraries with proper order for static linking
target_link_libraries(${PROJECT_NAME}
    AWS::aws-lambda-runtime
    ${AWSSDK_LINK_LIBRARIES}
    ${CURL_LIBRARIES}
    ZLIB::ZLIB
    ${DUCKDB_LIBRARY}
)

# Precompiled DuckDB includes all internal dependencies

# Add system libraries and DuckDB dependencies
target_link_libraries(${PROJECT_NAME}
    stdc++
    dl
    pthread
    m
    rt
)

# Link DuckDB extensions if found separately
if(DUCKDB_FTS_LIBRARY)
    target_link_libraries(${PROJECT_NAME} ${DUCKDB_FTS_LIBRARY})
    message(STATUS "Linking with separate FTS library: ${DUCKDB_FTS_LIBRARY}")
else()
    message(STATUS "Using FTS extension built into DuckDB library")
endif()

if(DUCKDB_VSS_LIBRARY)
    target_link_libraries(${PROJECT_NAME} ${DUCKDB_VSS_LIBRARY})
    message(STATUS "Linking with separate VSS library: ${DUCKDB_VSS_LIBRARY}")
else()
    message(STATUS "Using VSS extension built into DuckDB library")
endif()

# Install the bootstrap binary
install(TARGETS ${PROJECT_NAME} DESTINATION bin)

# Copy the bootstrap binary to dist directory for CDK deployment
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_CURRENT_SOURCE_DIR}/dist
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:${PROJECT_NAME}> ${CMAKE_CURRENT_SOURCE_DIR}/dist/bootstrap
    COMMENT "Copying bootstrap to dist directory"
)
