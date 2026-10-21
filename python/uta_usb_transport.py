#!/usr/bin/env python3
"""UTA Linux USB transaction worker. JSON-lines API, libusb-1.0, no pip deps.

Open an explicitly selected /dev/bus/usb node. `exchange` posts its IN request
before ordered OUT packets; it NEVER pipelines separate protocol commands.
Every OUT completion is checked before returning received bytes. No retries.
"""
import argparse
import ctypes as C
import json
import re
import sys
import time


def packets(data, chunk_size):
    if not 1 <= chunk_size <= 512 or len(data) > 16384:
        raise ValueError('Invalid packet size or frame exceeds 16 KiB')
    result = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
    if len(data) % chunk_size == 0:
        result.append(b'')
    return result


class Transfer(C.Structure):
    pass


Callback = C.CFUNCTYPE(None, C.POINTER(Transfer))
Transfer._fields_ = [('dev_handle', C.c_void_p), ('flags', C.c_ubyte),
    ('endpoint', C.c_ubyte), ('type', C.c_ubyte), ('timeout', C.c_uint),
    ('status', C.c_int), ('length', C.c_int), ('actual_length', C.c_int),
    ('callback', Callback), ('user_data', C.c_void_p), ('buffer', C.c_void_p),
    ('num_iso_packets', C.c_int)]


class Timeval(C.Structure):
    _fields_ = [('sec', C.c_long), ('usec', C.c_long)]


class USB:
    def __init__(self):
        if sys.platform != 'linux':
            raise RuntimeError('This worker runs on Linux')
        self.lib = C.CDLL('libusb-1.0.so.0')
        p = C.c_void_p
        signatures = {
            'init': ([C.POINTER(p)], C.c_int), 'exit': ([p], None),
            'get_device_list': ([p, C.POINTER(C.POINTER(p))], C.c_ssize_t),
            'free_device_list': ([C.POINTER(p), C.c_int], None),
            'get_bus_number': ([p], C.c_ubyte), 'get_device_address': ([p], C.c_ubyte),
            'get_device_descriptor': ([p, p], C.c_int),
            'open': ([p, C.POINTER(p)], C.c_int), 'close': ([p], None),
            'claim_interface': ([p, C.c_int], C.c_int), 'release_interface': ([p, C.c_int], C.c_int),
            'alloc_transfer': ([C.c_int], C.POINTER(Transfer)),
            'free_transfer': ([C.POINTER(Transfer)], None),
            'submit_transfer': ([C.POINTER(Transfer)], C.c_int),
            'cancel_transfer': ([C.POINTER(Transfer)], C.c_int),
            'handle_events_timeout_completed': ([p, C.POINTER(Timeval), C.POINTER(C.c_int)], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, 'libusb_'+name); fn.argtypes=args; fn.restype=result
        self.ctx, self.handle = p(), p()
        self.iface = None
        self.failed = False
        self.check(self.lib.libusb_init(C.byref(self.ctx)), 'init')

    @staticmethod
    def check(rc, label):
        if rc < 0:
            raise RuntimeError(f'{label}: libusb {rc}; no retry')

    def open(self, path, vid, pid, iface):
        if self.handle:
            raise ValueError('Already open')
        match = re.fullmatch(r'/dev/bus/usb/(\d{3})/(\d{3})', path)
        if not match or not 0 <= iface <= 255:
            raise ValueError('Select an exact USB node and interface')
        bus, address = map(int, match.groups())
        devices=C.POINTER(C.c_void_p)()
        n=self.lib.libusb_get_device_list(self.ctx,C.byref(devices))
        self.check(n,'list')
        try:
            for i in range(n):
                dev=devices[i]
                if self.lib.libusb_get_bus_number(dev)!=bus or self.lib.libusb_get_device_address(dev)!=address:
                    continue
                desc=C.create_string_buffer(18)
                self.check(self.lib.libusb_get_device_descriptor(dev,desc),'descriptor')
                if int.from_bytes(desc.raw[8:10],'little')!=vid or int.from_bytes(desc.raw[10:12],'little')!=pid:
                    raise RuntimeError('Selected USB identity changed')
                self.check(self.lib.libusb_open(dev,C.byref(self.handle)),'open')
                self.check(self.lib.libusb_claim_interface(self.handle,iface),'claim')
                self.iface=iface
                return
            raise RuntimeError('Selected USB node disappeared')
        finally:
            self.lib.libusb_free_device_list(devices,1)

    def transfer(self, outgoing=None, output=None, input=None, size=4096, chunk=64, timeout=60000):
        if not self.handle or self.failed:
            raise RuntimeError('USB session unavailable; reconnect explicitly')
        if not 1 <= size <= 65536 or not 1 <= timeout <= 60000:
            raise ValueError('Invalid read size/timeout')
        if input is not None and not 0x81 <= input <= 0x8f:
            raise ValueError('Invalid bulk IN endpoint')
        if outgoing is not None and (output is None or not 1 <= output <= 15):
            raise ValueError('Invalid bulk OUT endpoint')
        chunks=packets(outgoing,chunk) if outgoing is not None else []
        specs=([(input,bytes(size),True)] if input is not None else [])+[(output,b,False) for b in chunks]
        if not specs:
            raise ValueError('Empty transfer')
        pending, allocations, results = {}, [], {}
        errors=[]
        @Callback
        def done(ptr):
            key=C.addressof(ptr.contents)
            entry=pending.pop(key)
            _,buf,is_in=entry
            t=ptr.contents
            if t.status != 0 or (not is_in and t.actual_length != t.length):
                errors.append(f'USB completion status={t.status}, actual={t.actual_length}/{t.length}; outcome unknown, no retry')
            elif is_in:
                results['data']=buf.raw[:t.actual_length]
        try:
            for endpoint,data,is_in in specs:
                ptr=self.lib.libusb_alloc_transfer(0)
                if not ptr: raise MemoryError('libusb allocation failed')
                buf=C.create_string_buffer(data,max(1,len(data)))
                allocations.append((ptr,buf))
                t=ptr.contents
                t.dev_handle=self.handle; t.flags=0; t.endpoint=endpoint; t.type=2
                t.timeout=timeout; t.length=len(data); t.buffer=C.cast(buf,C.c_void_p)
                t.callback=done; t.user_data=None; t.num_iso_packets=0
                key=C.addressof(t)
                pending[key]=(ptr,buf,is_in)
                rc=self.lib.libusb_submit_transfer(ptr)
                if rc:
                    pending.pop(key); self.check(rc,'submit')
            while pending:
                tv=Timeval(0,100000)
                self.check(self.lib.libusb_handle_events_timeout_completed(self.ctx,C.byref(tv),None),'events')
                if errors: raise RuntimeError(errors[0])
            if errors: raise RuntimeError(errors[0])
            return results.get('data',b'')
        except BaseException:
            self.failed=True
            for ptr,_,_ in list(pending.values()): self.lib.libusb_cancel_transfer(ptr)
            deadline=time.monotonic()+5
            while pending and time.monotonic()<deadline:
                tv=Timeval(0,100000)
                self.lib.libusb_handle_events_timeout_completed(self.ctx,C.byref(tv),None)
            if pending:
                # Pending libusb transfers own buffers. Let OS process teardown
                # reclaim them rather than freeing memory beneath a callback.
                import os
                os.write(2,b'USB cancellation did not complete; terminating worker\n')
                os._exit(78)
            raise
        finally:
            for ptr,_ in allocations: self.lib.libusb_free_transfer(ptr)

    def close(self):
        if self.handle:
            if self.iface is not None: self.lib.libusb_release_interface(self.handle,self.iface)
            self.lib.libusb_close(self.handle); self.handle=C.c_void_p()
        if self.ctx:
            self.lib.libusb_exit(self.ctx); self.ctx=C.c_void_p()


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    usb=USB()
    try:
        for line in sys.stdin:
            try:
                if len(line)>150000: raise ValueError('Request too large')
                q=json.loads(line); op=q['op']
                if op=='open':
                    usb.open(q['path'],q['vid'],q['pid'],q['iface']); result={}
                elif op in ('write','read','exchange'):
                    data=bytes.fromhex(q['hex']) if op!='read' else None
                    received=usb.transfer(outgoing=data,output=q.get('out'),input=q.get('in') if op!='write' else None,
                                          size=q.get('size',4096),chunk=q.get('chunk',64),timeout=q.get('timeout',60000))
                    result={'hex':received.hex(),'written':len(data) if data is not None else 0}
                elif op=='close':
                    print(json.dumps({'ok':True,'result':{}}),flush=True);break
                else: raise ValueError('Unknown operation')
                print(json.dumps({'ok':True,'result':result}),flush=True)
            except Exception as e:
                print(json.dumps({'ok':False,'error':str(e)}),flush=True)
    finally:
        usb.close()


if __name__=='__main__':
    main()
