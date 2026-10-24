import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8');
const apiSource = source.slice(source.indexOf('async function api('), source.indexOf('\nfunction toast('));
let response;
const context = vm.createContext({fetch: async () => response, me: null});
vm.runInContext(apiSource, context);
response = new Response('<!DOCTYPE html><h1>Resource limit</h1>', {status: 503, headers: {'Content-Type':'text/html'}});
await assert.rejects(context.api('/api/login', {username:'fixture',password:'fixture'}), /portal could not complete/);
response = Response.json({error:'Incorrect student username or password.'}, {status:401});
await assert.rejects(context.api('/api/login', {username:'fixture',password:'fixture'}), /Incorrect student username/);
response = Response.json({ok:true});
assert.equal((await context.api('/api/me')).ok, true);
console.log('PASS: HTML failure, JSON authentication failure, and JSON success handling.');
