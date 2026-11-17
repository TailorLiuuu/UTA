#!/usr/bin/env python3
"""UI-only fixture; no SSH, USB, local config or client credentials."""
import base64,json,sys,time
args=sys.argv[1:]
assert args[:1]==['port']
assert args[args.index('--to')+1]=='a'*32
action=args[1]
if action=='list':
    result=[dict(id='b'*32,session='d'*32,state='attached',nodes=['/dev/ttyTEST'],busid='9-1',vidpid='abcd:1234',access='owner',busy=False),
            dict(id='c'*32,session='e'*32,state='attached',nodes=['/dev/ttyOTHER'],busid='9-2',vidpid='abcd:5678',access='shared',busy=False)]
elif action=='logs':
    raw='测试日志：中\n'.encode()
    print(json.dumps(dict(event='execution',run_id='f'*32)),file=sys.stderr,flush=True)
    for data in [raw[:1],raw[1:]]:
        print(json.dumps(dict(event='serial',data=base64.b64encode(data).decode())),file=sys.stderr,flush=True)
    time.sleep(.1)
    result=dict(state='completed',exit_code=None)
else:raise RuntimeError('Unexpected fixture operation')
print(json.dumps(dict(ok=True,result=result)))
