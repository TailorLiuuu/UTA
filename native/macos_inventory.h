#pragma once
#include <cstdint>
#include <string>
#include <vector>

struct MacUSBDevice {
    std::string busid, name, manufacturer, serial, vidpid;
    uint64_t registry_id;
    uint32_t location;
    int device_class, speed;
};

// Registry properties only: no USB user client, descriptor request or libusb init.
std::vector<MacUSBDevice> macos_usb_inventory();
