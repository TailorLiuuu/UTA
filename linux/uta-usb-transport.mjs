// UTA Linux transport adapter for protocol plugins. Requires Node 18+.
// The caller selects an exact cloud node and validates the device protocol.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

export class UtaUsbTransport {
  static async open(options) {
    const transport = new UtaUsbTransport(options);
    try {
      await transport.rpc({op:'open',path:options.path,vid:options.vid,pid:options.pid,iface:options.iface});
      return transport;
    } catch (error) { await transport.close(); throw error; }
  }
  constructor({input,output,packetSize,worker=fileURLToPath(new URL('./uta-usb-io',import.meta.url)),python='python3'}) {
    if (!Number.isInteger(packetSize) || packetSize<1 || packetSize>512) throw Error('Specify device-compatible OUT packetSize (1..512)');
    this.input=input;this.output=output;this.packetSize=packetSize;
    this.pending=null;this.closed=false;this.failure=null;
    this.child=spawn(python,[worker],{stdio:['pipe','pipe','inherit']});
    this.exited=new Promise(resolve=>this.child.once('close',resolve));
    this.lines=createInterface({input:this.child.stdout});
    this.lines.on('line',line=>{
      const pending=this.pending;this.pending=null;
      if(!pending){this.fail(Error('Unexpected USB worker reply'));return;}
      clearTimeout(pending.timer);
      try {const r=JSON.parse(line);if(!r.ok)throw Error(r.error);pending.resolve(r.result);}
      catch(error){this.failure=error;pending.reject(error);}
    });
    this.child.on('error',error=>this.fail(error));
    this.child.on('close',code=>{this.closed=true;this.fail(Error(`USB worker exited (${code}); no retry`));});
    this.child.stdin.on('error',error=>this.fail(error));
  }
  fail(error){
    this.failure??=error;
    if(this.pending){clearTimeout(this.pending.timer);this.pending.reject(error);this.pending=null;}
  }
  rpc(request){
    if(this.pending)return Promise.reject(Error('Concurrent protocol transactions are forbidden'));
    if(this.closed || (this.failure && request.op!=='close'))return Promise.reject(this.failure??Error('USB worker closed'));
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.fail(Error('USB worker timeout; outcome unknown, no retry'));this.child.kill();},(request.timeout??60000)+10000);
      this.pending={resolve,reject,timer};
      this.child.stdin.write(JSON.stringify(request)+'\n');
    });
  }
  async write(data){
    await this.rpc({op:'write',out:this.output,chunk:this.packetSize,hex:Buffer.from(data).toString('hex')});
  }
  async read(timeout=60000){
    const r=await this.rpc({op:'read',in:this.input,size:16384,timeout:Math.min(60000,Math.ceil(timeout))});
    return new Uint8Array(Buffer.from(r.hex,'hex'));
  }
  async exchange(data,timeout=60000){
    const r=await this.rpc({op:'exchange',out:this.output,in:this.input,size:16384,chunk:this.packetSize,
      timeout:Math.min(60000,Math.ceil(timeout)),hex:Buffer.from(data).toString('hex')});
    return new Uint8Array(Buffer.from(r.hex,'hex'));
  }
  async close(){
    if(this.closed)return;
    // An in-flight command has an uncertain outcome: terminate, never replay.
    if(this.pending){this.fail(Error('USB session closed during a command'));this.child.kill();}
    else {try{await this.rpc({op:'close',timeout:1000});}catch{}this.child.stdin.end();}
    const timer=setTimeout(()=>this.child.kill('SIGKILL'),3000);
    await this.exited;clearTimeout(timer);this.lines.close();this.closed=true;
  }
}
