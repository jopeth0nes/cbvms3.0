/** Release wrapper: original.mjs is a verified backup of the deployed Worker. */
import original from './original.mjs';
import {campusRegistration} from './registration.mjs';
import {campusRecords} from './records.mjs';
import {patchPortalScript} from './decision-display.mjs';
export {PasswordCrypto} from './original.mjs';

export default {
  async fetch(request: Request, env: any, context: ExecutionContext) {
    const path = new URL(request.url).pathname;
    if(path === '/app.js' && request.method === 'GET') {
      const assetHeaders = new Headers(request.headers);
      assetHeaders.delete('If-None-Match'); assetHeaders.delete('If-Modified-Since');
      const response = await original.fetch(new Request(request,{headers:assetHeaders}),env,context);
      if(response.status !== 200)return response;
      const source = await response.text();
      const headers = new Headers(response.headers);
      headers.delete('Content-Length'); headers.delete('ETag'); headers.delete('Content-Encoding');
      headers.set('Cache-Control','no-store');
      return new Response(patchPortalScript(source),{status:200,headers});
    }
    if (!['/api/campus/registrations','/api/campus/records'].includes(path))
      return original.fetch(request, env, context);
    const response = await (path === '/api/campus/records' ? campusRecords : campusRegistration)(request, env);
    const headers = new Headers(response.headers);
    headers.set('X-Content-Type-Options', 'nosniff');
    headers.set('Referrer-Policy', 'no-referrer');
    headers.set('X-Frame-Options', 'DENY');
    return new Response(response.body, {status: response.status, headers});
  },
};
