import { cp, mkdir, rm } from 'node:fs/promises';
await rm('dist', { recursive:true, force:true });
await mkdir('dist/assets', { recursive:true });
await cp('flowledger/static/index.html','dist/index.html');
await cp('flowledger/static/app.js','dist/assets/app.js');
await cp('flowledger/static/style.css','dist/assets/style.css');
