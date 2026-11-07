#pragma once
#include <memory>
#include <mutex>
#include <system_error>

namespace usbipdcpp {
// Admission limits, not a bandwidth throttle. Account before allocating and
// retain the reservation through USB completion and response transmission.
struct ResourceBudget {
    std::mutex mutex;
    std::size_t bytes = 0, requests = 0;
    const std::size_t max_bytes, max_requests;
    ResourceBudget(std::size_t b = 64 * 1024 * 1024, std::size_t r = 1024)
        : max_bytes(b), max_requests(r) {}
};

struct TransferReservation {
    std::shared_ptr<ResourceBudget> session;
    std::shared_ptr<ResourceBudget> process;
    std::size_t bytes;
    bool admitted = false;
    ~TransferReservation() {
        if (!admitted) return;
        std::scoped_lock lock(session->mutex, process->mutex);
        session->bytes -= bytes; --session->requests;
        process->bytes -= bytes; --process->requests;
    }
    static std::shared_ptr<TransferReservation> acquire(
            std::shared_ptr<ResourceBudget> session, std::size_t bytes) {
        static auto process = std::make_shared<ResourceBudget>(256 * 1024 * 1024, 4096);
        auto lease = std::make_shared<TransferReservation>();
        lease->session = std::move(session); lease->process = process; lease->bytes = bytes;
        std::scoped_lock lock(lease->session->mutex, process->mutex);
        auto &s = *lease->session;
        if (s.requests >= s.max_requests || process->requests >= process->max_requests ||
            bytes > s.max_bytes - s.bytes || bytes > process->max_bytes - process->bytes)
            throw std::system_error(std::make_error_code(std::errc::no_buffer_space), "USB session resource limit");
        s.bytes += bytes; ++s.requests;
        process->bytes += bytes; ++process->requests;
        lease->admitted = true;
        return lease;
    }
};
}
