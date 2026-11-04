#include "macos_inventory.h"
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBHostFamilyDefinitions.h>
#include <algorithm>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>

namespace {
struct Entry {
    io_object_t value = 0;
    ~Entry() { if (value) IOObjectRelease(value); }
};
struct Property {
    CFTypeRef value;
    Property(io_registry_entry_t entry, CFStringRef key)
        : value(IORegistryEntryCreateCFProperty(entry, key, kCFAllocatorDefault, 0)) {}
    ~Property() { if (value) CFRelease(value); }
};
uint64_t number(io_registry_entry_t entry, CFStringRef key) {
    Property p(entry, key);
    int64_t value = 0;
    if (p.value && CFGetTypeID(p.value) == CFNumberGetTypeID())
        CFNumberGetValue(static_cast<CFNumberRef>(p.value), kCFNumberSInt64Type, &value);
    return static_cast<uint64_t>(value);
}
std::string string(io_registry_entry_t entry, CFStringRef key) {
    Property p(entry, key);
    if (!p.value || CFGetTypeID(p.value) != CFStringGetTypeID()) return {};
    auto text = static_cast<CFStringRef>(p.value);
    std::string value(CFStringGetMaximumSizeForEncoding(CFStringGetLength(text), kCFStringEncodingUTF8) + 1, '\0');
    if (!CFStringGetCString(text, value.data(), value.size(), kCFStringEncodingUTF8)) return {};
    value.resize(std::char_traits<char>::length(value.c_str()));
    return value;
}
unsigned port(io_registry_entry_t entry) {
    auto value = number(entry, CFSTR("PortNum"));
    if (value) return static_cast<unsigned>(value);
    Entry parent;
    if (IORegistryEntryGetParentEntry(entry, kIOServicePlane, &parent.value) != KERN_SUCCESS) return 0;
    Property p(parent.value, CFSTR("port"));
    if (p.value && CFGetTypeID(p.value) == CFDataGetTypeID() && CFDataGetLength(static_cast<CFDataRef>(p.value)))
        return CFDataGetBytePtr(static_cast<CFDataRef>(p.value))[0];
    return 0;
}
uint64_t parent_session(io_registry_entry_t entry) {
    // Match libusb's topology using USB-plane parent session IDs; location nibbles
    // alone cannot represent all hub port numbers.
    io_registry_entry_t current = entry;
    IOObjectRetain(current);
    for (int depth = 0; depth < 32; ++depth) {
        io_registry_entry_t parent = 0;
        auto result = IORegistryEntryGetParentEntry(current, "IOUSB", &parent);
        IOObjectRelease(current);
        if (result != KERN_SUCCESS) return 0;
        current = parent;
        auto session = number(current, CFSTR("sessionID"));
        if (session) { IOObjectRelease(current); return session; }
    }
    IOObjectRelease(current);
    return 0;
}
}

std::vector<MacUSBDevice> macos_usb_inventory() {
    Entry iterator;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOUSBHostDevice"), &iterator.value) != KERN_SUCCESS)
        throw std::runtime_error("Cannot read USB registry");
    struct Record { MacUSBDevice device; uint64_t session, parent; unsigned port, address; };
    std::vector<Record> records;
    std::map<uint64_t, size_t> sessions;
    while (auto service = IOIteratorNext(iterator.value)) {
        Entry entry{service};
        uint64_t registry_id = 0;
        if (IORegistryEntryGetRegistryEntryID(service, &registry_id) != KERN_SUCCESS) continue;
        const auto location = static_cast<uint32_t>(number(service, CFSTR("locationID")));
        const auto cls = static_cast<int>(number(service, CFSTR("bDeviceClass")));
        const auto raw_speed = number(service, CFSTR("USBSpeed"));
        // USBSpeed is IOUSBHostConnectionSpeed, not the legacy IOUSBLib enum.
        int speed = 0;
        switch (raw_speed) {
            case kIOUSBHostConnectionSpeedLow: speed = 1; break;
            case kIOUSBHostConnectionSpeedFull: speed = 2; break;
            case kIOUSBHostConnectionSpeedHigh: speed = 3; break;
            case kIOUSBHostConnectionSpeedSuper: speed = 5; break;
            case kIOUSBHostConnectionSpeedSuperPlus:
            case kIOUSBHostConnectionSpeedSuperPlusBy2: speed = 6; break;
        }
        std::ostringstream id;
        id << std::hex << std::setfill('0') << std::setw(4) << number(service, CFSTR("idVendor"))
           << ':' << std::setw(4) << number(service, CFSTR("idProduct"));
        auto name = string(service, CFSTR("USB Product Name"));
        auto serial = string(service, CFSTR("USB Serial Number"));
        if (serial.empty()) serial = string(service, CFSTR("kUSBSerialNumberString"));
        records.push_back({{"", name.empty() ? "USB Device" : name,
            string(service, CFSTR("USB Vendor Name")), serial, id.str(), registry_id, location, cls, speed},
            number(service, CFSTR("sessionID")), parent_session(service), port(service),
            static_cast<unsigned>(number(service, CFSTR("USB Address")))});
        if (records.back().session) sessions[records.back().session] = records.size() - 1;
    }
    std::vector<MacUSBDevice> result;
    for (auto &record : records) {
        if (record.device.device_class == 9) continue;
        std::vector<unsigned> ports;
        const Record *current = &record;
        for (int depth = 0; current && current->port && depth < 8; ++depth) {
            ports.push_back(current->port);
            auto parent = sessions.find(current->parent);
            current = parent == sessions.end() ? nullptr : &records[parent->second];
        }
        std::reverse(ports.begin(), ports.end());
        auto busid = std::to_string(record.device.location >> 24);
        for (size_t i = 0; i < ports.size(); ++i) busid += (i ? "." : "-") + std::to_string(ports[i]);
        if (ports.empty()) busid += "-" + std::to_string(record.address) + ":" + std::to_string(record.port);
        record.device.busid = busid;
        result.push_back(record.device);
    }
    std::sort(result.begin(), result.end(), [](const auto &a, const auto &b) { return a.busid < b.busid; });
    return result;
}
