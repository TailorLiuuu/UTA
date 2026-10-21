#include <usbipdcpp/LibusbHandler/LibusbServer.h>
#include <chrono>
#include <thread>
#include <sstream>
#include <iomanip>
#include <unistd.h>
#include <sys/socket.h>

// Executed inside the already-loaded, signed helper. No root subprocess or
// runtime executable paths. Owns fd/logfd and releases the device on peer EOF.
extern "C" __attribute__((visibility("default")))
int uta_export_stream(int fd, int logfd, const char *busid, const char *vidpid, const char *serial) {
    int outcome = 1;
    bool adopted = false;
    auto report = [&](const std::string &message) {
        auto line = message + "\n";
        write(logfd, line.data(), line.size());
    };
    if (libusb_init(nullptr)) {
        report("libusb initialization failed");
        close(fd); close(logfd); return 1;
    }
    try {
        usbipdcpp::LibusbServer server({.skip_hub = true, .auto_bind_hotplug = false});
        auto *device = usbipdcpp::LibusbServer::find_by_busid(busid);
        if (!device) throw std::runtime_error("Selected USB is no longer present");
        libusb_device_descriptor d{};
        libusb_get_device_descriptor(device, &d);
        std::ostringstream id;
        id << std::hex << std::setw(4) << std::setfill('0') << d.idVendor << ":" << std::setw(4) << d.idProduct;
        libusb_device_handle *handle{};
        auto opened = libusb_open(device, &handle);
        unsigned char value[256]{};
        int length = handle && d.iSerialNumber ? libusb_get_string_descriptor_ascii(handle, d.iSerialNumber, value, sizeof(value)) : 0;
        if (handle) libusb_close(handle);
        while (length > 0 && value[length - 1] == 0) --length;
        std::string actual(length > 0 ? reinterpret_cast<char *>(value) : "", length > 0 ? length : 0);
        if (opened || id.str() != vidpid || (*serial && actual != serial)) {
            libusb_unref_device(device);
            throw std::runtime_error(opened ? libusb_strerror(opened) : "Selected USB identity changed");
        }
        auto bound = server.bind_host_device(device); // owns reference, including failure
        if (bound != usbipdcpp::DeviceOperationResult::Success)
            throw std::runtime_error("USB bind failed: " + std::to_string(int(bound)));
        auto error = server.start_stream(fd);
        if (error) throw std::runtime_error(error.message());
        adopted = true;
        report("Authorized USB stream started");
        while (server.get_server().get_session_count() > 0)
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        server.stop();
        report("USB stream closed; interface release completed");
        outcome = 0;
    } catch (const std::exception &error) {
        report(error.what());
    }
    if (!adopted) close(fd);
    close(logfd);
    libusb_exit(nullptr);
    return outcome;
}
