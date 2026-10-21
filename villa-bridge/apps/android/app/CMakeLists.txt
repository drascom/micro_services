cmake_minimum_required(VERSION 3.22.1)
project(villa_bridge_android)

add_library(villa_node SHARED src/main/cpp/node-runtime.cpp)

add_library(libnode SHARED IMPORTED)
set_target_properties(
    libnode
    PROPERTIES IMPORTED_LOCATION
    "${NODE_MOBILE_DIR}/bin/${ANDROID_ABI}/libnode.so"
)

target_include_directories(
    villa_node
    PRIVATE "${NODE_MOBILE_DIR}/include/node"
)

find_library(log_library log)

target_link_libraries(
    villa_node
    libnode
    ${log_library}
)
