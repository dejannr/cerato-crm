import test from 'node:test';
import assert from 'node:assert/strict';
import { createHandler } from '../relay/handler.mjs';

function socketWith(replies, written) {
  const encoder = new TextEncoder();
  const readable = new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(replies)); controller.close(); } });
  const writable = new WritableStream({ write(chunk) { written.push(new TextDecoder().decode(chunk)); } });
  return {readable, writable, close:async()=>{}};
}
const payload = {username:'stefan@ceratosystems.com',password:'secret',recipient:'recipient@example.com',raw_email:btoa('From: stefan@ceratosystems.com\r\n\r\nHello\r\n.leading\r\n')};
function request(body=payload,token='secret-token') {return new Request('https://relay.example/send',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)});}

test('requires authentication before connecting',async()=>{
 let opened=false; const handler=createHandler(()=>{opened=true});
 const result=await handler.fetch(request(payload,'wrong'),{RELAY_TOKEN:'secret-token'});
 assert.equal(result.status,401); assert.equal(opened,false);
});
test('uses fixed SMTP server and reports success only after final acknowledgement',async()=>{
 let writes=[];
 const handler=createHandler((host,opts)=>{assert.deepEqual(host,{hostname:'smtp.purelymail.com',port:465});assert.equal(opts.secureTransport,'on');return socketWith('220 ready\r\n250-test\r\n250 AUTH PLAIN\r\n235 authenticated\r\n250 sender\r\n250 recipient\r\n354 go ahead\r\n250 accepted\r\n',writes)});
 const result=await handler.fetch(request(),{RELAY_TOKEN:'secret-token'}); assert.equal((await result.json()).status,'sent');
 assert.ok(writes.some(x=>x.includes('\r\n..leading\r\n.\r\n')));
});
test('rejected recipient is not sent',async()=>{
 const handler=createHandler(()=>socketWith('220 ready\r\n250 ok\r\n235 ok\r\n250 ok\r\n550 rejected\r\n',[]));
 const result=await handler.fetch(request(),{RELAY_TOKEN:'secret-token'});assert.equal((await result.json()).status,'rejected');
});
test('connection lost after DATA is uncertain',async()=>{
 const handler=createHandler(()=>socketWith('220 ready\r\n250 ok\r\n235 ok\r\n250 ok\r\n250 ok\r\n354 data\r\n',[]));
 const result=await handler.fetch(request(),{RELAY_TOKEN:'secret-token'});assert.equal((await result.json()).status,'uncertain');
});
test('does not allow arbitrary sending identities',async()=>{
 let opened=false;const handler=createHandler(()=>{opened=true});
 const result=await handler.fetch(request({...payload,username:'other@example.com'}),{RELAY_TOKEN:'secret-token'});
 assert.equal(result.status,400);assert.equal(opened,false);
});
test('verify authenticates but does not issue a send command',async()=>{
 const writes=[];const handler=createHandler(()=>socketWith('220 ready\r\n250 ok\r\n235 authenticated\r\n',writes));
 const r=new Request('https://relay.example/verify',{method:'POST',headers:{Authorization:'Bearer secret-token','Content-Type':'application/json'},body:JSON.stringify({username:payload.username,password:payload.password})});
 const result=await handler.fetch(r,{RELAY_TOKEN:'secret-token'});assert.equal((await result.json()).status,'authenticated');
 assert.equal(writes.some(x=>x.startsWith('MAIL FROM:')),false);
});
