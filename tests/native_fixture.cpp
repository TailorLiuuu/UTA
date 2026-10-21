// Explicit synthetic protocol fixture, never shipped in the app.
// Tests the same native USB/IP core without claiming any physical USB device.
#include <usbipdcpp/Server.h>
#include <usbipdcpp/DeviceHandler/DeviceHandler.h>
#include <usbipdcpp/virtual_device/TransferResponder.h>
#include <iostream>
#include <thread>
#include <unistd.h>
#include <spdlog/sinks/stdout_sinks.h>
#include <usbipdcpp/LibusbHandler/LibusbTransferOperator.h>
#include <libusb.h>

using namespace usbipdcpp;
class Echo : public AbstDeviceHandler {
    data_type last;
public:
    explicit Echo(UsbDevice &dev) : AbstDeviceHandler(dev) {}
    void receive_urb(UsbIpCommand::UsbIpCmdSubmit cmd, UsbEndpoint,
                     std::optional<UsbInterface>, error_code &) override {
        auto *t = GenericTransfer::from_handle(cmd.transfer.get());
        if (cmd.header.direction == 0) {
            last = t->data;
            responder->submit_ret_submit(UsbIpResponse::UsbIpRetSubmit::
                create_ret_submit_ok_without_data(cmd.header.seqnum, cmd.transfer_buffer_length));
        } else {
            auto length = std::min(last.size(), size_t(cmd.transfer_buffer_length));
            std::copy_n(last.begin(), length, t->data.begin());
            t->actual_length = length;
            responder->submit_ret_submit(UsbIpResponse::UsbIpRetSubmit::
                create_ret_submit_ok_with_no_iso(cmd.header.seqnum, length, std::move(cmd.transfer)));
        }
    }
    void handle_unlink_seqnum(uint32_t, uint32_t seq) override {
        responder->submit_ret_unlink(UsbIpResponse::UsbIpRetUnlink::create_ret_unlink_success(seq));
    }
};
int main(int argc, char **argv) {
    spdlog::set_default_logger(spdlog::stderr_logger_mt("fixture"));
    if (argc == 2 && std::string(argv[1]) == "--libusb-control") {
        asio::io_context io;
        asio::ip::tcp::socket socket(io);
        socket.assign(asio::ip::tcp::v4(), STDIN_FILENO);
        LibusbTransferOperator op;
        for (unsigned sequence = 1; sequence <= 3; ++sequence) {
            UsbIpHeaderBasic header{};
            header.ep = 0;
            SetupPacket setup{};
            auto *trx = static_cast<libusb_transfer *>(op.alloc_transfer_handle(18, 0, header, setup));
            libusb_fill_control_setup(trx->buffer, sequence == 2 ? 0 : 0x80, 6, 0x100, 0,
                                      sequence == 3 ? 0 : 18);
            libusb_fill_control_transfer(trx, nullptr, trx->buffer, nullptr, nullptr, 1000);
            trx->actual_length = sequence == 3 ? 0 : 18;
            for (int i = 0; i < 18; ++i) trx->buffer[8 + i] = i + 1;
            auto reply = UsbIpResponse::UsbIpRetSubmit::create_ret_submit(
                sequence, 0, trx->actual_length, 0, 0, TransferHandle(trx, &op));
            error_code ec;
            reply.to_socket(socket, ec);
            if (ec) return 2;
        }
        return 0;
    }
    std::vector<UsbInterface> interfaces{
        UsbInterface{.interface_class = 0xff, .endpoints = {{
            UsbEndpoint{.address = 0x81, .attributes = 2, .max_packet_size = 64},
            UsbEndpoint{.address = 0x02, .attributes = 2, .max_packet_size = 64}}}}};
    auto device = UsbDevice::make("99-1", 0x1234, 0x5678, interfaces);
    device->with_handler<Echo>();
    Server server;
    server.add_device(std::move(device));
    if (argc == 2 && std::string(argv[1]) == "--stream") {
        if (server.start_stream(STDIN_FILENO)) return 1;
        std::cout << "ready" << std::endl;
        while (server.get_session_count() > 0) std::this_thread::sleep_for(std::chrono::milliseconds(10));
        server.stop();
        return 0;
    }
    if (server.start({asio::ip::make_address("127.0.0.1"), 0})) return 1;
    std::cout << server.endpoint().port() << std::endl;
    std::string input;
    std::getline(std::cin, input);
    server.stop();
}
