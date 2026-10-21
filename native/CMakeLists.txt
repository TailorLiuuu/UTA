cmake_minimum_required(VERSION 3.22)
project(UTANative LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "14.0")
set(ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../vendor")
find_package(PkgConfig REQUIRED)
pkg_check_modules(USB REQUIRED IMPORTED_TARGET libusb-1.0)
foreach(DIR IN LISTS USB_INCLUDE_DIRS)
    get_filename_component(USB_PARENT "${DIR}" DIRECTORY)
    include_directories("${USB_PARENT}")
endforeach()
file(GLOB CORE "${ROOT}/usbipdcpp/src/*.cpp"
    "${ROOT}/usbipdcpp/src/utils/*.cpp"
    "${ROOT}/usbipdcpp/src/DeviceHandler/*.cpp"
    "${ROOT}/usbipdcpp/src/InterfaceHandler/*.cpp"
    "${ROOT}/usbipdcpp/src/LibusbHandler/*.cpp")
add_library(uta_core OBJECT ${CORE} privileged_stream.cpp)
set_target_properties(uta_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(uta_core PUBLIC
    "${ROOT}/usbipdcpp/include" "${ROOT}/asio" "${ROOT}/spdlog")
target_compile_definitions(uta_core PUBLIC ASIO_STANDALONE
    SPDLOG_FMT_EXTERNAL=0 SPDLOG_USE_STD_FORMAT
    PRIVATE USBIPDCPP_EXPORTS SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_INFO)
target_link_libraries(uta_core PUBLIC PkgConfig::USB)
add_library(uta_transport SHARED)
target_link_libraries(uta_transport PUBLIC uta_core)
add_library(uta_capture STATIC)
target_link_libraries(uta_capture PUBLIC uta_core)
add_executable(uta-native main.cpp)
target_link_libraries(uta-native PRIVATE uta_transport)
set_target_properties(uta-native PROPERTIES BUILD_RPATH "@loader_path")
add_executable(uta-test-fixture ../tests/native_fixture.cpp)
target_link_libraries(uta-test-fixture PRIVATE uta_transport)
