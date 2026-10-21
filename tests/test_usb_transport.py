import ctypes as C
import unittest
from test_bridge import load
worker = load('usb_worker', 'uta_usb_transport.py')


class FakeUSB:
    def __init__(self, fail=None):
        self.transfers=[]; self.pending=[]; self.freed=[]; self.cancelled=[]; self.fail=fail
    def libusb_alloc_transfer(self, n):
        p=C.pointer(worker.Transfer()); self.transfers.append(p); return p
    def libusb_submit_transfer(self, p):
        self.pending.append(p); return 0
    def libusb_handle_events_timeout_completed(self, *args):
        # IN deliberately completes first; caller must still check every OUT.
        p=self.pending.pop(0); t=p.contents
        t.status=3 if C.addressof(t) in self.cancelled else 0
        t.actual_length=t.length
        if t.endpoint & 0x80:
            C.memmove(t.buffer,b'ACK',3); t.actual_length=3
        elif self.fail=='short':
            t.actual_length=max(0,t.length-1); self.fail=None
        t.callback(p); return 0
    def libusb_cancel_transfer(self,p):
        self.cancelled.append(C.addressof(p.contents)); return 0
    def libusb_free_transfer(self,p): self.freed.append(C.addressof(p.contents))


class USBTests(unittest.TestCase):
    def usb(self,fail=None):
        u=worker.USB.__new__(worker.USB); u.lib=FakeUSB(fail)
        u.handle=C.c_void_p(1); u.ctx=C.c_void_p(1); u.failed=False
        return u
    def test_packet_boundary_and_zlp(self):
        self.assertEqual([len(p) for p in worker.packets(bytes(99),64)], [64,35])
        self.assertEqual([len(p) for p in worker.packets(bytes(128),64)], [64,64,0])
        with self.assertRaises(ValueError): worker.packets(bytes(16385),64)
    def test_prepost_and_queue_entire_frame_and_check_all_completions(self):
        u=self.usb(); data=bytes(range(99))
        self.assertEqual(u.transfer(data,1,0x81),b'ACK')
        self.assertEqual([p.contents.endpoint for p in u.lib.transfers],[0x81,1,1])
        self.assertEqual(len(u.lib.freed),3)
        self.assertFalse(u.failed)
    def test_short_write_cancels_remaining_and_never_retries(self):
        u=self.usb('short')
        with self.assertRaisesRegex(RuntimeError,'outcome unknown'):
            u.transfer(bytes(256),1,0x81)
        self.assertTrue(u.failed)
        self.assertEqual(len(u.lib.transfers),6)
        self.assertEqual(len(u.lib.freed),6)
        self.assertEqual(len(u.lib.cancelled),4)
        with self.assertRaisesRegex(RuntimeError,'reconnect explicitly'):
            u.transfer(b'retry',1,0x81)
        self.assertEqual(len(u.lib.transfers),6)
