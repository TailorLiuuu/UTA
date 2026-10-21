#include <usbipdcpp/LibusbHandler/LibusbServer.h>
#include <usbipdcpp/LibusbHandler/tools.h>
#include <spdlog/sinks/stdout_sinks.h>
#include <spdlog/spdlog.h>
#include <csignal>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <thread>
#include <unistd.h>
#include <sys/socket.h>
#include <regex>

static volatile sig_atomic_t stopped = 0;
static void stop_signal(int) { stopped = 1; }
static std::string json_quote(const std::string &s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') o << '\\' << c;
        else if (c < 32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        else o << c;
    }
    o << '"';
    return o.str();
}
static std::string usb_string(libusb_device_handle *h, uint8_t index) {
    unsigned char text[256]{};
    if (!h || !index) return "";
    int n = libusb_get_string_descriptor_ascii(h, index, text, sizeof(text));
    if (n <= 0) return "";
    while (n > 0 && text[n - 1] == 0) --n;
    return std::string(reinterpret_cast<char *>(text), n);
}
int main(int argc, char **argv) {
    spdlog::set_default_logger(spdlog::stderr_logger_mt("uta"));
    spdlog::set_level(spdlog::level::info);
    if (libusb_init(nullptr) != 0) {
        std::cerr << "libusb initialization failed\n";
        return 1;
    }
    int result = 0;
    try {
        if (argc == 2 && std::string(argv[1]) == "list") {
            libusb_device **devices{};
            auto count = libusb_get_device_list(nullptr, &devices);
            if (count < 0) throw std::runtime_error(libusb_error_name(int(count)));
            std::cout << "[";
            bool first = true;
            for (ssize_t i = 0; i < count; ++i) {
                libusb_device_descriptor d{};
                if (libusb_get_device_descriptor(devices[i], &d) != 0 || d.bDeviceClass == 9) continue;
                libusb_device_handle *h{};
                libusb_open(devices[i], &h);
                auto product = usb_string(h, d.iProduct);
                auto manufacturer = usb_string(h, d.iManufacturer);
                auto serial = usb_string(h, d.iSerialNumber);
                if (h) libusb_close(h);
                if (product.empty()) product = "USB Device";
                std::ostringstream vidpid;
                vidpid << std::hex << std::setw(4) << std::setfill('0') << d.idVendor
                       << ":" << std::setw(4) << d.idProduct;
                if (!first) std::cout << ",";
                first = false;
                std::cout << "{\"busid\":" << json_quote(usbipdcpp::get_device_busid(devices[i]))
                    << ",\"name\":" << json_quote(product) << ",\"manufacturer\":" << json_quote(manufacturer)
                    << ",\"serial\":" << json_quote(serial) << ",\"vidpid\":" << json_quote(vidpid.str())
                    << ",\"class\":" << int(d.bDeviceClass)
                    << ",\"speed\":" << int(usbipdcpp::libusb_speed_to_usb_speed(libusb_get_device_speed(devices[i])))
                    << "}";
            }
            std::cout << "]\n";
            libusb_free_device_list(devices, 1);
        } else if ((argc == 3 && std::string(argv[1]) == "export") ||
                   (argc == 5 && std::string(argv[1]) == "export-stream")) {
            const bool stream = std::string(argv[1]) == "export-stream";
            if (!std::regex_match(argv[2], std::regex("[0-9]+-[0-9.]+")))
                throw std::runtime_error("Invalid busid");
            // Each process exports exactly one explicitly selected device. Never bind all.
            usbipdcpp::LibusbServer server({.skip_hub = true, .auto_bind_hotplug = false});
            auto *device = usbipdcpp::LibusbServer::find_by_busid(argv[2]);
            if (!device) throw std::runtime_error("USB device is no longer present");
            if (stream) {
                libusb_device_descriptor desc{};
                libusb_get_device_descriptor(device, &desc);
                std::ostringstream identity;
                identity << std::hex << std::setw(4) << std::setfill('0') << desc.idVendor
                         << ":" << std::setw(4) << desc.idProduct;
                libusb_device_handle *handle{};
                libusb_open(device, &handle);
                auto serial = usb_string(handle, desc.iSerialNumber);
                if (handle) libusb_close(handle);
                if (identity.str() != argv[3] || (!std::string(argv[4]).empty() && serial != argv[4])) {
                    libusb_unref_device(device);
                    throw std::runtime_error("Selected USB identity changed; select the device again");
                }
                int type = 0; socklen_t size = sizeof(type);
                if (getsockopt(STDIN_FILENO, SOL_SOCKET, SO_TYPE, &type, &size) || type != SOCK_STREAM) {
                    libusb_unref_device(device);
                    throw std::runtime_error("A connected IPC stream is required");
                }
            }
            auto bound = server.bind_host_device(device); // takes ownership of reference
            if (bound != usbipdcpp::DeviceOperationResult::Success)
                throw std::runtime_error("Cannot export USB device, result=" + std::to_string(int(bound)));
            auto ec = stream ? server.start_stream(STDIN_FILENO)
                             : server.start({asio::ip::make_address("127.0.0.1"), 0});
            if (ec) throw std::runtime_error(ec.message());
            std::signal(SIGINT, stop_signal);
            std::signal(SIGTERM, stop_signal);
            if (!stream) std::cout << "{\"port\":" << server.get_server().endpoint().port()
                      << ",\"busid\":" << json_quote(argv[2]) << "}" << std::endl;
            const pid_t parent = getppid();
            while (!stopped && getppid() == parent && (!stream || server.get_server().get_session_count() > 0))
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            server.stop();
        } else {
            std::cerr << "Usage: uta-native list | export BUSID\n";
            result = 2;
        }
    } catch (const std::exception &e) {
        std::cerr << e.what() << "\n";
        result = 1;
    }
    libusb_exit(nullptr);
    return result;
}
