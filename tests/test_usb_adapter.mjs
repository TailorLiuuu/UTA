import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {UtaUsbTransport} from '../linux/uta-usb-transport.mjs';

async function fixture(t,extra='') {
  const root=await mkdtemp(join(tmpdir(),'uta-worker-test-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const worker=join(root,'worker.py');
  await writeFile(worker,`import sys,json,time
for line in sys.stdin:
 q=json.loads(line)
 if q['op']=='close':
  print(json.dumps({'ok':True,'result':{}}),flush=True);break
 if q['op']=='exchange': time.sleep(0.03)
 ${extra||"print(json.dumps({'ok':True,'result':{'hex':'41434b'}}),flush=True)"}
`);
  return {path:'/dev/bus/usb/001/002',vid:0x3563,pid:0x1000,iface:0,input:0x81,output:1,packetSize:64,worker};
}

test('exchange returns bytes; no concurrent protocol commands; worker closes',async t=>{
  const usb=await UtaUsbTransport.open(await fixture(t));t.after(()=>usb.close());
  const pending=usb.exchange(new Uint8Array([1,2,3]));
  await assert.rejects(usb.write(new Uint8Array([4])),/Concurrent/);
  assert.equal(Buffer.from(await pending).toString(),'ACK');
  await usb.close();assert.equal(usb.closed,true);
});
test('worker error latches and forbids subsequent writes',async t=>{
  const options=await fixture(t,"print(json.dumps({'ok':True,'result':{}}) if q['op']=='open' else json.dumps({'ok':False,'error':'outcome unknown'}),flush=True)");
  const usb=await UtaUsbTransport.open(options);t.after(()=>usb.close());
  await assert.rejects(usb.exchange(new Uint8Array([1])),/outcome unknown/);
  await assert.rejects(usb.write(new Uint8Array([2])),/outcome unknown/);
});
test('open failure cleans up child process',async t=>{
  await assert.rejects(UtaUsbTransport.open(await fixture(t,"print(json.dumps({'ok':False,'error':'wrong VID/PID'}),flush=True)")),/wrong VID/);
});
