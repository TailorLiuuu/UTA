#include <usbipdcpp/LibusbHandler/LibusbDeviceHandler.h>
#include <cassert>
#include <thread>
#include <vector>
#include <iostream>

int main() {
    usbipdcpp::UsbDevice a{}, b{};
    usbipdcpp::LibusbDeviceHandler first(a, static_cast<libusb_device *>(nullptr));
    usbipdcpp::LibusbDeviceHandler second(b, static_cast<libusb_device *>(nullptr));
    std::vector<std::string> one, two;
    first.import_diagnostic_ = [&](const auto &line) { one.push_back(line); };
    second.import_diagnostic_ = [&](const auto &line) { two.push_back(line); };
    std::thread t1([&] { for (int i = 0; i < 100; ++i) first.report_import_error("claim", 32, LIBUSB_ERROR_INVALID_PARAM); });
    std::thread t2([&] { for (int i = 0; i < 100; ++i) second.report_import_error("claim", 0, LIBUSB_ERROR_BUSY); });
    t1.join(); t2.join();
    assert(one.size() == 100 && two.size() == 100);
    for (const auto &line : one) assert(line.find("interface=32 error=LIBUSB_ERROR_INVALID_PARAM") != std::string::npos);
    for (const auto &line : two) assert(line.find("interface=0 error=LIBUSB_ERROR_BUSY") != std::string::npos);
    first.import_diagnostic_ = [](const auto &) { throw 1; };
    first.report_import_error("claim", 32, LIBUSB_ERROR_ACCESS); // cleanup must still run
    std::cout << "Concurrent export diagnostics remain isolated; failing sink contained\n";
}
