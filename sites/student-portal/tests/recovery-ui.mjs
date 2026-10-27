import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const here = fileURLToPath(new URL('.', import.meta.url));
const publicDir = join(here, '..', 'public');
const moduleBase = process.env.PLAYWRIGHT_MODULE_DIR;
const require = moduleBase
  ? createRequire(join(moduleBase, 'playwright-loader.cjs'))
  : createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch { /* Optional: run this browser check where Playwright is already available. */ }

const json = (response, status, value) => {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify(value));
};

test('mocked password recovery works by keyboard and fits desktop and mobile viewports', {
  skip: !chromium && 'Playwright is unavailable; set PLAYWRIGHT_MODULE_DIR to an existing Playwright node_modules directory.',
}, async t => {
  const fixture = { requests: [], verify: [], complete: [], htmlFailure: false, signedIn: false, emailVerified: false, emailRequests: [] };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/api/me') return fixture.signedIn
      ? json(response,200,{name:'Synthetic student',username:'fixture',csrf:'fixture-csrf',must_change_password:false,preferences:{}})
      : json(response, 401, { error: 'Unauthenticated' });
    if(url.pathname==='/api/page')return json(response,200,{_student:{email:'saved@example.com'},_violations:[],_notifications:[]});
    if(url.pathname==='/api/recovery/email'&&request.method==='GET')return json(response,200,{verified:fixture.emailVerified,email:fixture.emailVerified?'saved@example.com':null,registered_email:'saved@example.com'});
    if (url.pathname.startsWith('/api/recovery/')) {
      if (request.method !== 'POST') return json(response, 405, { error: 'Method not allowed' });
      let body = '';
      for await (const chunk of request) body += chunk;
      const payload = JSON.parse(body);
      if(url.pathname==='/api/recovery/email/request'){
        fixture.emailRequests.push({payload,csrf:request.headers['x-csrf-token']});
        return json(response,200,{challenge_id:'saved-email-fixture',resend_after:60,expires_in:600});
      }
      if(url.pathname==='/api/recovery/email/verify'){
        if(payload.challenge_id!=='saved-email-fixture'||payload.code!=='123456')return json(response,400,{error:'Invalid code'});
        fixture.emailVerified=true;return json(response,200,{ok:true});
      }
      if (url.pathname.endsWith('/request')) {
        if (fixture.htmlFailure) {
          response.writeHead(503, { 'Content-Type': 'text/html' });
          response.end('<h1>temporary upstream failure</h1>');
          return;
        }
        fixture.requests.push(payload);
        return json(response, 200, {
          message: 'If this account is eligible for recovery, a code will be sent to its registered email.',
          challenge_id: `fixture-challenge-${fixture.requests.length}`,
          resend_after: 60,
          expires_in: 600,
        });
      }
      if (url.pathname.endsWith('/verify')) {
        fixture.verify.push(payload);
        return payload.code === '123456'
          ? json(response, 200, { grant: 'fixture-grant', expires_in: 300 })
          : json(response, 400, { error: 'Invalid or expired code.' });
      }
      if (url.pathname.endsWith('/complete')) {
        fixture.complete.push(payload);
        return payload.grant === 'fixture-grant' && payload.password === payload.confirmation
          ? json(response, 200, { ok: true })
          : json(response, 400, { error: 'Password reset rejected.' });
      }
      return json(response, 404, { error: 'Not found' });
    }

    const relative = url.pathname === '/' ? 'portal.html' : url.pathname.slice(1);
    if (!['portal.html', 'app.js', 'style.css'].includes(relative)) {
      response.writeHead(404).end();
      return;
    }
    const content = await readFile(join(publicDir, relative));
    const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };
    response.writeHead(200, { 'Content-Type': types[extname(relative)] });
    response.end(content);
  });

  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const launchOptions = process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : { channel: 'chrome' };
  const browser = await chromium.launch({ headless: true, ...launchOptions });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();

  await page.goto(`${baseUrl}/?recovery=1`);
  await page.getByRole('heading', { name: 'Reset your password' }).waitFor();
  assert.equal(fixture.requests.length, 0, 'opening recovery must not send a request');
  const username = page.getByLabel('Username or student ID');
  assert.equal(await username.getAttribute('maxlength'), '200');
  assert.match(await page.locator('.muted').first().innerText(), /campus SMTP computer online/i);
  const desktopCard = await page.locator('.login .card').boundingBox();
  assert.ok(desktopCard.x > 0 && desktopCard.x + desktopCard.width < 1440
    && desktopCard.y >= 0 && desktopCard.y + desktopCard.height <= 900,
  'desktop card fits within the viewport');

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileRequestCard = await page.locator('.login .card').boundingBox();
  assert.ok(mobileRequestCard.x >= 0 && mobileRequestCard.x + mobileRequestCard.width <= 390
    && mobileRequestCard.y >= 0 && mobileRequestCard.y + mobileRequestCard.height <= 844,
  'mobile request card stays within the viewport');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, 'mobile request screen does not overflow horizontally');
  await username.fill('2026-00123');
  await username.press('Enter');
  await page.getByRole('heading', { name: 'Check your email' }).waitFor();
  assert.deepEqual(fixture.requests, [{ username: '2026-00123' }]);
  const resend = page.getByRole('button', { name: 'Resend code in 60s' });
  assert.equal(await resend.isDisabled(), true);
  await page.setViewportSize({ width: 1440, height: 900 });
  const desktopCodeCard = await page.locator('.login .card').boundingBox();
  assert.ok(desktopCodeCard.x > 0 && desktopCodeCard.x + desktopCodeCard.width < 1440
    && desktopCodeCard.y >= 0 && desktopCodeCard.y + desktopCodeCard.height <= 900,
  'desktop code card fits within the viewport');
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileCodeCard = await page.locator('.login .card').boundingBox();
  assert.ok(mobileCodeCard.x >= 0 && mobileCodeCard.x + mobileCodeCard.width <= 390
    && mobileCodeCard.y >= 0 && mobileCodeCard.y + mobileCodeCard.height <= 844,
  'mobile code card stays within the viewport');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, 'mobile code screen does not overflow horizontally');
  const code = page.getByLabel('6-digit verification code');
  await page.evaluate(() => navigator.clipboard.writeText('1a2b3c4d5e6'));
  await code.click();
  await page.keyboard.press('ControlOrMeta+V');
  assert.equal(await code.inputValue(), '123456', 'the one-time-code field strips non-digits and caps pasted input');
  await code.press('Enter');
  await page.getByRole('heading', { name: 'Choose a new password' }).waitFor();
  assert.ok(await page.getByRole('link', { name: 'Return to sign in' }).isVisible(), 'password step offers a return to sign in');
  await page.setViewportSize({ width: 1440, height: 900 });
  const desktopPasswordCard = await page.locator('.login .card').boundingBox();
  assert.ok(desktopPasswordCard.x > 0 && desktopPasswordCard.x + desktopPasswordCard.width < 1440
    && desktopPasswordCard.y >= 0 && desktopPasswordCard.y + desktopPasswordCard.height <= 900,
  'desktop password card fits within the viewport');
  const password = page.locator('#recovery-complete input[name="password"]');
  const confirmation = page.locator('#recovery-complete input[name="confirmation"]');
  assert.equal(await password.getAttribute('maxlength'), '1024');
  assert.equal(await confirmation.getAttribute('maxlength'), '1024');
  await page.getByRole('button', { name: 'Show new password' }).click();
  assert.equal(await password.getAttribute('type'), 'text');
  await page.getByRole('button', { name: 'Hide new password' }).click();
  assert.equal(await password.getAttribute('type'), 'password');

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileCard = await page.locator('.login .card').boundingBox();
  assert.ok(mobileCard.x >= 0 && mobileCard.x + mobileCard.width <= 390
    && mobileCard.y >= 0 && mobileCard.y + mobileCard.height <= 844,
  'mobile password card stays within the viewport');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, 'mobile recovery does not overflow horizontally');
  await password.fill('safe fixture passphrase');
  await confirmation.fill('different fixture passphrase');
  await confirmation.press('Enter');
  await page.getByText('Passwords do not match.').waitFor();
  assert.equal(fixture.complete.length, 0, 'mismatched passwords are not submitted');
  await confirmation.fill('safe fixture passphrase');
  await confirmation.press('Enter');
  await page.getByRole('heading', { name: 'Student Portal' }).waitFor();
  await page.getByText('Password updated. Sign in with your new password.').waitFor();
  assert.deepEqual(fixture.verify, [{ challenge_id: 'fixture-challenge-1', code: '123456' }]);
  assert.equal(fixture.complete.length, 1);
  assert.deepEqual(fixture.complete[0], {
    password: 'safe fixture passphrase', confirmation: 'safe fixture passphrase', grant: 'fixture-grant',
  });

  fixture.htmlFailure = true;
  await page.goto(`${baseUrl}/?recovery=1`);
  await page.getByLabel('Username or student ID').fill('student-fixture');
  await page.getByRole('button', { name: 'Send recovery code' }).click();
  await page.getByText('Recovery is temporarily unavailable. Please try again shortly.').waitFor();
  assert.equal(await page.getByText('temporary upstream failure').count(), 0, 'HTML upstream errors stay generic');
  fixture.htmlFailure=false;
  const beforeResume=fixture.requests.length;
  await page.goto(baseUrl+'/?recovery=1#challenge='+'a'.repeat(64));
  await page.getByRole('heading',{name:'Check your email',exact:true}).waitFor();
  assert.equal(fixture.requests.length,beforeResume,'opening an issued challenge link must not send another code');
  await page.getByRole('button',{name:'Request a new code',exact:true}).click();
  await page.getByRole('heading',{name:'Reset your password',exact:true}).waitFor();

  // Existing authenticated session, forgotten password: verify saved contact,
  // then enter recovery directly while the session remains signed in.
  fixture.htmlFailure=false;fixture.signedIn=true;
  await page.goto(baseUrl+'/');
  await page.getByRole('button',{name:'My Profile',exact:true}).click();
  await page.getByRole('button',{name:'Verify my saved email',exact:true}).click();
  await page.locator('#recovery-email-code').waitFor();
  assert.deepEqual(fixture.emailRequests,[{payload:{use_registered_email:true},csrf:'fixture-csrf'}]);
  await page.locator('#recovery-email-code input[name="code"]').fill('123456');
  await page.getByRole('button',{name:'Confirm recovery email',exact:true}).click();
  await page.getByText('Verified recovery address: saved@example.com',{exact:true}).waitFor();
  await page.getByRole('link',{name:'Reset your forgotten password',exact:true}).click();
  await page.getByRole('heading',{name:'Reset your password',exact:true}).waitFor();

});
