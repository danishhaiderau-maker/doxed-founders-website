import assert from 'node:assert/strict';
import test from 'node:test';
import { createHmac } from 'node:crypto';
import { BitfinexAuthTradeStream, buildBitfinexWsAuth, parseBitfinexAuthTradeMessage } from './bitfinex-auth-trade-stream';

class FakeSocket {
  readyState = 1; sent: string[] = [];
  listeners = new Map<string, Array<(event: any) => void>>();
  addEventListener(type: string, fn: (event: any) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]); }
  send(data: string) { this.sent.push(data); }
  close() { this.emit('close', {}); }
  emit(type: string, event: any) { for (const fn of this.listeners.get(type) ?? []) fn(event); }
}

test('Bitfinex WS auth signs AUTH nonce without exposing secret', () => {
  const auth = buildBitfinexWsAuth({ apiKey: 'key', apiSecret: 'secret' }, 123);
  assert.equal(auth.authPayload, 'AUTH123');
  assert.equal(auth.authSig, createHmac('sha384', 'secret').update('AUTH123').digest('hex'));
  assert.equal(JSON.stringify(auth).includes('secret'), false);
});

test('production socket factory does not depend on a global WebSocket', () => {
  const saved = (globalThis as any).WebSocket;
  try {
    (globalThis as any).WebSocket = undefined;
    const stream = new BitfinexAuthTradeStream(
      { apiKey: 'runtime-key', apiSecret: 'runtime-secret' },
      () => true,
    );
    assert.doesNotThrow(() => stream.start());
    stream.stop();
  } finally {
    (globalThis as any).WebSocket = saved;
  }
});

test('auth trade parser strictly validates event, symbol and numeric types', () => {
  const row = [11, 'tBTCF0:USTF0', 1_000, 42, -0.004, 64_000];
  assert.equal(parseBitfinexAuthTradeMessage([0, 'te', row], 1_100)?.orderId, 42);
  assert.equal(parseBitfinexAuthTradeMessage([0, 'tu', row], 1_100)?.receivedAtMs, 1_100);
  assert.equal(parseBitfinexAuthTradeMessage([0, 'te', ['11', ...row.slice(1)]]), null);
  assert.equal(parseBitfinexAuthTradeMessage([0, 'te', [11, 'tETHF0:USTF0', ...row.slice(2)]]), null);
});

test('stream authenticates, deduplicates te/tu and aggregates partial fills', async () => {
  const socket = new FakeSocket(); const trades: any[] = [];
  const stream = new BitfinexAuthTradeStream({ apiKey: 'key', apiSecret: 'secret' }, (trade) => { trades.push(trade); return true; }, () => socket, () => 2_000);
  stream.start(); socket.emit('open', {});
  assert.equal(JSON.parse(socket.sent[0]).event, 'auth');
  const ready = stream.waitUntilReady();
  socket.emit('message', { data: JSON.stringify({ event: 'auth', status: 'OK' }) });
  assert.equal(await ready, true);
  const first = [11, 'tBTCF0:USTF0', 1_000, 42, -0.004, 64_000];
  socket.emit('message', { data: JSON.stringify([0, 'te', first]) });
  socket.emit('message', { data: JSON.stringify([0, 'tu', first]) });
  socket.emit('message', { data: JSON.stringify([0, 'te', [12, 'tBTCF0:USTF0', 1_100, 42, -0.006, 63_900]]) });
  await Promise.resolve();
  assert.equal(trades.length, 2); assert.equal(trades[0].cumulativeQty, 0.004);
  assert.equal(trades[1].cumulativeQty, 0.01); assert.ok(Math.abs(trades[1].cumulativeAveragePrice - 63_940) < 1e-8);
  stream.stop();
});

test('stream reconnects after close with bounded backoff', () => {
  const sockets: FakeSocket[] = []; const original = global.setTimeout;
  let delay = 0; let callback: (() => void) | null = null;
  global.setTimeout = ((fn: () => void, ms: number) => { callback = fn; delay = ms; return { unref() {} } as any; }) as any;
  try {
    const stream = new BitfinexAuthTradeStream({ apiKey: 'k', apiSecret: 's' }, () => true, () => { const s = new FakeSocket(); sockets.push(s); return s; });
    stream.start(); sockets[0].emit('close', {});
    assert.ok(delay >= 500 && delay < 750); (callback as unknown as () => void)();
    assert.equal(sockets.length, 2); stream.stop();
  } finally { global.setTimeout = original; }
});

test('timed-out readiness waiter removes itself', async () => {
  const socket = new FakeSocket();
  const stream = new BitfinexAuthTradeStream({ apiKey: 'wait-k', apiSecret: 'wait-s' }, () => true, () => socket);
  stream.start();
  assert.equal(await stream.waitUntilReady(2), false);
  assert.equal((stream as any).readyWaiters.length, 0);
  stream.stop();
});

test('every reconnect allocates a strictly newer shared nonce', () => {
  const sockets: FakeSocket[] = []; const original = global.setTimeout;
  let callback: (() => void) | null = null;
  global.setTimeout = ((fn: () => void) => { callback = fn; return { unref() {} } as any; }) as any;
  try {
    const stream = new BitfinexAuthTradeStream({ apiKey: 'reconnect-nonce', apiSecret: 's' }, () => true, () => { const s = new FakeSocket(); sockets.push(s); return s; });
    stream.start(); sockets[0].emit('open', {}); const first = BigInt(JSON.parse(sockets[0].sent[0]).authNonce);
    sockets[0].emit('close', {}); (callback as unknown as () => void)(); sockets[1].emit('open', {});
    const second = BigInt(JSON.parse(sockets[1].sent[0]).authNonce);
    assert.ok(second > first); stream.stop();
  } finally { global.setTimeout = original; }
});

test('stale authenticated socket is closed for reconnect', () => {
  const socket = new FakeSocket(); const originalInterval = global.setInterval;
  let staleCheck: (() => void) | null = null; let closes = 0;
  socket.close = () => { closes += 1; };
  global.setInterval = ((fn: () => void) => { staleCheck = fn; return { unref() {} } as any; }) as any;
  try {
    let now = 1_000;
    const stream = new BitfinexAuthTradeStream({ apiKey: 'stale-k', apiSecret: 's' }, () => true, () => socket, () => now);
    stream.start(); socket.emit('open', {}); socket.emit('message', { data: JSON.stringify({ event: 'auth', status: 'OK' }) });
    now += 45_001; (staleCheck as unknown as () => void)();
    assert.equal(closes, 1); stream.stop();
  } finally { global.setInterval = originalInterval; }
});

test('auth rejection backoff grows until auth OK, not merely socket open', () => {
  const sockets: FakeSocket[]=[]; const delays:number[]=[]; const callbacks:(()=>void)[]=[];
  const original=global.setTimeout; const originalRandom=Math.random; Math.random=()=>0;
  global.setTimeout=((fn:()=>void,ms:number)=>{callbacks.push(fn);delays.push(ms);return{unref(){}} as any}) as any;
  try {
    const stream=new BitfinexAuthTradeStream({apiKey:'reject-k',apiSecret:'s'},()=>true,()=>{const s=new FakeSocket();sockets.push(s);return s});
    stream.start(); sockets[0].emit('open',{}); sockets[0].emit('message',{data:JSON.stringify({event:'auth',status:'FAILED'})});
    assert.equal(delays[0],500); callbacks.shift()!(); sockets[1].emit('open',{}); sockets[1].emit('message',{data:JSON.stringify({event:'auth',status:'FAILED'})});
    assert.equal(delays[1],1000); stream.stop();
  } finally {global.setTimeout=original;Math.random=originalRandom}
});

test('failed callback keeps trade retryable and concurrent tu is replayed once', async () => {
  const socket=new FakeSocket(); let calls=0; let release!:()=>void;
  const first=new Promise<void>(resolve=>{release=resolve});
  const stream=new BitfinexAuthTradeStream({apiKey:'retry-k',apiSecret:'s'},async()=>{calls+=1;if(calls===1){await first;return false}return true},()=>socket);
  stream.start(); socket.emit('open',{}); socket.emit('message',{data:JSON.stringify({event:'auth',status:'OK'})});
  const row=[71,'tBTCF0:USTF0',1000,42,-.01,64000];
  socket.emit('message',{data:JSON.stringify([0,'te',row])}); socket.emit('message',{data:JSON.stringify([0,'tu',row])});
  release(); await new Promise(resolve=>setImmediate(resolve)); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,2); stream.stop();
});

test('stream cache cleanup is TTL bounded and preserves active work and recent partial orders', () => {
  const socket=new FakeSocket(); const stream=new BitfinexAuthTradeStream({apiKey:'clean-k',apiSecret:'s'},()=>true,()=>socket) as any;
  const now=100*60*60_000;
  stream.seenTradeIds.set(1,now-2*60*60_000); stream.preparedTrades.set(1,{receivedAtMs:now-2*60*60_000});
  stream.seenTradeIds.set(2,now-2*60*60_000); stream.preparedTrades.set(2,{receivedAtMs:now-2*60*60_000}); stream.inFlightTradeIds.add(2);
  stream.orderAggregates.set(10,{qty:.01,notional:640,lastAtMs:now-25*60*60_000});
  stream.orderAggregates.set(11,{qty:.01,notional:640,lastAtMs:now-2*60*60_000});
  stream.pruneCaches(now);
  assert.equal(stream.seenTradeIds.has(1),false); assert.equal(stream.preparedTrades.has(1),false);
  assert.equal(stream.seenTradeIds.has(2),true); assert.equal(stream.preparedTrades.has(2),true);
  assert.equal(stream.orderAggregates.has(10),false); assert.equal(stream.orderAggregates.has(11),true);
});
