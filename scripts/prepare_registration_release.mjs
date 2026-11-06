/** Compile only the new module and wrapper; keep deployed code unchanged. */
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const require = createRequire(new URL('../.runtime/cloud-tools/package.json',import.meta.url));
const {transform} = require('esbuild');
const root = new URL('../', import.meta.url);
const release = new URL('.runtime/registration-release/', root);
for (const [input,output] of [
  ['sites/student-portal/lib/campus-registration.ts','registration.mjs'],
  ['sites/student-portal/lib/campus-records.ts','records.mjs'],
  ['sites/student-portal/lib/portal-decision-display.ts','decision-display.mjs'],
  ['sites/student-portal/cloudflare/registration-wrapper.ts','worker.js'],
]) {
  const source = await readFile(new URL(input,root),'utf8');
  const compiled = await transform(source,{loader:'ts',format:'esm',target:'es2022'});
  await writeFile(new URL(output,release),compiled.code);
}
const original = await readFile(new URL('original.mjs',release));
await writeFile(new URL('original.sha256',release),createHash('sha256').update(original).digest('hex'));
console.log('Prepared additive registration release; deployed Worker code retained unchanged.');
