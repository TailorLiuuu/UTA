file(GLOB_RECURSE src *.cpp)
add_executable(libusb_windows_service ${src})
target_link_libraries(libusb_windows_service usbipdcpp_libusb)
target_include_directories(libusb_windows_service PRIVATE .)
