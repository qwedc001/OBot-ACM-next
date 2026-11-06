import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import pngjs from 'pngjs';
import { validate, build } from './build.mjs';
const spec = () => ({ paper: { title: 'Test paper', pages: 2 }, slides: [
  { kind: 'title', title: 'Test paper', subtitle: 'Authors', pages: [1] },
  { kind: 'bullets', title: 'Conclusion', bullets: ['A sourced finding.'], pages: [2] }
] });
test('valid; reject unsourced, oversized, and unsupported input', () => {
  validate(spec());
  for (const change of [s => s.slides[0].pages = [], s => s.slides[0].pages = [3],
    s => s.slides[1].bullets = ['x'.repeat(151)], s => s.slides[1].kind = 'svg',
    s => s.slides[0].title = 'x\nhello', s => s.paper.pages = 201]) {
    const s = spec(); change(s); assert.throws(() => validate(s));
  }
});
test('build actual ZIP, exclusive output, block asset traversal and URLs', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'paper-ppt-'));
  try {
    const src = path.join(dir, 'spec.json'), out = path.join(dir, 'deck.pptx');
    await fs.writeFile(src, JSON.stringify(spec()));
    assert.equal((await build(src, out)).slides, 2);
    assert.equal((await fs.readFile(out)).subarray(0, 2).toString(), 'PK');
    await assert.rejects(build(src, out), /EEXIST/);
    for (const image of ['/etc/passwd', 'https://example.com/a.png', '../outside.png']) {
      const s = spec(); s.slides.push({ kind: 'visual', title: 'Figure', caption: 'A figure',
        pages: [1], image: { path: image, page: 1 } });
      await fs.writeFile(src, JSON.stringify(s));
      await assert.rejects(build(src, path.join(dir, 'bad.pptx')));
    }
  } finally { await fs.rm(dir, { recursive: true }); }
});

test('positive visual and reject truncated/corrupt PNGs', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'paper-visual-'));
  try {
    const src = path.join(dir, 'spec.json'), image = path.join(dir, 'crop.png');
    const png = new pngjs.PNG({ width: 10, height: 10 }); png.data.fill(255);
    const valid = pngjs.PNG.sync.write(png);
    const s = spec(); s.slides.push({ kind: 'visual', title: 'Method', caption: 'A real crop',
      pages: [1], image: { path: 'crop.png', page: 1 } });
    await fs.writeFile(src, JSON.stringify(s)); await fs.writeFile(image, valid);
    assert.equal((await build(src, path.join(dir, 'ok.pptx'))).slides, 3);
    const corrupt = Buffer.from(valid); corrupt[corrupt.length - 1] ^= 1;
    for (const bad of [valid.subarray(0, 24), corrupt]) {
      await fs.writeFile(image, bad);
      await assert.rejects(build(src, path.join(dir, 'bad.pptx')));
    }
  } finally { await fs.rm(dir, { recursive: true }); }
});
