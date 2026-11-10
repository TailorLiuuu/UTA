/* Compile against the actual patched libusbi.h; never initialize USB. */
#include "libusbi.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    usbi_interface_mask mask = {0};
    assert(USB_MAXINTERFACES == 256);
    assert(usbi_interface_empty(mask));
    const unsigned int sparse[] = {0, 1, 2, 3, 4, 32, 63, 64, 127, 128, 191, 192, 255};
    for (unsigned int k = 0; k < sizeof(sparse)/sizeof(*sparse); ++k) {
        usbi_interface_set(&mask, sparse[k]);
        for (unsigned int n = 0; n < USB_MAXINTERFACES; ++n) {
            int expected = 0;
            for (unsigned int j = 0; j <= k; ++j) expected |= n == sparse[j];
            assert(!!usbi_interface_test(mask, n) == expected);
        }
    }
    usbi_interface_mask saved = mask;
    memset(&mask, 0, sizeof(mask));
    assert(usbi_interface_empty(mask));
    mask = saved; /* reset/capture restores every claimed interface */
    for (unsigned int n = 0; n < USB_MAXINTERFACES; ++n) usbi_interface_clear(&mask, n);
    assert(usbi_interface_empty(mask));
    usbi_interface_set(&mask, 256);
    usbi_interface_set(&mask, (unsigned int)-1);
    assert(usbi_interface_empty(mask));
    assert(!usbi_interface_test(saved, 256));
    assert(!usbi_interface_test(saved, (unsigned int)-1));
    // Exercise the linked core API's real bounds checks with a synthetic
    // disconnected handle. No libusb_init/open/claim backend or USB I/O.
    struct libusb_context context = {0};
    struct libusb_device device = {0};
    struct libusb_device_handle handle = {0};
    device.ctx = &context;
    handle.dev = &device;
    usbi_mutex_init(&handle.lock);
    for (int n = 0; n < USB_MAXINTERFACES; ++n) {
        assert(libusb_claim_interface(&handle, n) == LIBUSB_ERROR_NO_DEVICE);
        assert(libusb_release_interface(&handle, n) == LIBUSB_ERROR_NOT_FOUND);
    }
    assert(libusb_claim_interface(&handle, -1) == LIBUSB_ERROR_INVALID_PARAM);
    assert(libusb_claim_interface(&handle, 256) == LIBUSB_ERROR_INVALID_PARAM);
    assert(libusb_release_interface(&handle, -1) == LIBUSB_ERROR_INVALID_PARAM);
    assert(libusb_release_interface(&handle, 256) == LIBUSB_ERROR_INVALID_PARAM);
    // Idempotent claims use the bitmap, not the backend, at high numbers too.
    usbi_atomic_store(&device.attached, 1);
    handle.claimed_interfaces = saved;
    for (unsigned int k = 0; k < sizeof(sparse)/sizeof(*sparse); ++k)
        assert(libusb_claim_interface(&handle, sparse[k]) == LIBUSB_SUCCESS);
    usbi_mutex_destroy(&handle.lock);
    puts("Darwin interface bitmap 0..255: sparse claims, restore, cleanup and bounds passed");
    return 0;
}
