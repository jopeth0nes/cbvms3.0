import { DurableObject } from 'cloudflare:workers';
import { pbkdf2Async } from '@noble/hashes/pbkdf2';
import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex, hexToBytes } from '@noble/hashes/utils';

/** Internal-only CPU work. Never persists or logs passwords or derived keys. */
export class PasswordCrypto extends DurableObject {
  async fetch(request: Request): Promise<Response> {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/derive')
      return new Response('Not found', {status: 404});
    const data = await request.json() as {password?: unknown; salt?: unknown; iterations?: unknown};
    if (typeof data.password !== 'string' || data.password.length > 1024 ||
        typeof data.salt !== 'string' || !/^[0-9a-f]{32}$/.test(data.salt) ||
        typeof data.iterations !== 'number' || !Number.isInteger(data.iterations) ||
        data.iterations < 100000 || data.iterations > 2000000)
      return new Response('Invalid derivation', {status: 400});
    const value = await pbkdf2Async(sha256, data.password, hexToBytes(data.salt),
      {c: data.iterations, dkLen: 32, asyncTick: 10});
    return Response.json({value: bytesToHex(value)}, {headers: {'Cache-Control':'no-store'}});
  }
}
