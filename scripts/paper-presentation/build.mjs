import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import pptxgen from 'pptxgenjs';
import pngjs from 'pngjs';

const WIDTH = 13.333333, HEIGHT = 7.5;
const MAX_IMAGE = 10 * 1024 * 1024;
function check(ok, message) { if (!ok) throw new Error(message); }
function text(value, label, max) {
  check(typeof value === 'string' && value.trim().length > 0, `${label}: expected text`);
  check(!/[\x00-\x1f\x7f]/u.test(value), `${label}: control characters forbidden`);
  check([...value].reduce((n, c) => n + (/[^\x00-\x7f]/u.test(c) ? 2 : 1), 0) <= max,
    `${label}: too long; split/rewrite instead of shrinking text`);
  return value;
}
export function validate(spec) {
  check(spec && typeof spec === 'object', 'expected JSON object');
  text(spec.paper?.title, 'paper.title', 300);
  check(Number.isInteger(spec.paper.pages) && spec.paper.pages > 0 && spec.paper.pages <= 200,
    'paper.pages must be 1..200 (use PDF inspection index)');
  check(Array.isArray(spec.slides) && spec.slides.length > 0 && spec.slides.length <= 40,
    'expected 1..40 slides');
  for (const slide of spec.slides) {
    check(['title', 'bullets', 'visual'].includes(slide.kind), 'unknown slide kind');
    text(slide.title, 'slide.title', slide.kind === 'title' ? 68 : 52);
    check(Array.isArray(slide.pages) && slide.pages.length > 0 &&
      slide.pages.every(n => Number.isInteger(n) && n > 0 && n <= spec.paper.pages),
    'every slide needs valid one-based paper pages');
    if (slide.kind === 'title') text(slide.subtitle, 'subtitle', 150);
    if (slide.kind === 'bullets') {
      check(Array.isArray(slide.bullets) && slide.bullets.length > 0 && slide.bullets.length <= 4,
        'expected 1..4 parallel bullets');
      slide.bullets.forEach(b => text(b, 'bullet', 150));
    }
    if (slide.kind === 'visual') {
      text(slide.caption, 'caption', 110);
      check(typeof slide.image?.path === 'string' && slide.pages.includes(slide.image.page),
        'image requires path and cited page');
    }
  }
  return spec;
}
async function imageData(root, relative) {
  check(!path.isAbsolute(relative) && path.extname(relative).toLowerCase() === '.png',
    'images must be relative local PNG files');
  const base = await fs.realpath(root), target = await fs.realpath(path.resolve(root, relative));
  check(target.startsWith(base + path.sep), 'image escapes spec directory');
  const handle = await fs.open(target, 'r');
  try {
    const stat = await handle.stat();
    check(stat.isFile() && stat.size <= MAX_IMAGE && stat.size >= 24, 'image size/type invalid');
    const buf = await handle.readFile();
    check(buf.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])), 'not PNG');
    check(buf.toString('ascii', 12, 16) === 'IHDR', 'missing PNG header');
    const width = buf.readUInt32BE(16), height = buf.readUInt32BE(20);
    check(width > 0 && height > 0 && width * height <= 16000000, 'PNG dimensions exceed limit');
    // Decode all chunks and verify CRCs, not merely the untrusted IHDR.
    pngjs.PNG.sync.read(buf, { checkCRC: true });
    return { data: 'image/png;base64,' + buf.toString('base64'), width, height };
  } finally { await handle.close(); }
}
export async function build(specPath, outputPath) {
  check(path.extname(outputPath).toLowerCase() === '.pptx', 'output must be .pptx');
  const input = await fs.open(specPath, 'r');
  let spec;
  try {
    check((await input.stat()).size <= 1024 * 1024, 'spec exceeds 1 MiB');
    spec = validate(JSON.parse(await input.readFile('utf8')));
  } finally { await input.close(); }
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = 'Paper presentation';
  pptx.subject = spec.paper.title;
  pptx.title = spec.paper.title;
  pptx.lang = 'en-US';
  pptx.theme = { headFontFace: 'MiSans', bodyFontFace: 'MiSans', lang: 'en-US' };
  const boxes = [];
  const addText = (slide, value, box, options = {}) => {
    check(box.x >= 0 && box.y >= 0 && box.x + box.w <= WIDTH + 0.001 &&
      box.y + box.h <= HEIGHT, 'off-canvas object');
    boxes.push(box);
    slide.addText(value, { ...box, fontFace: 'MiSans', fontSize: 20, color: '263345',
      margin: 0, breakLine: false, valign: 'mid', ...options });
  };
  for (const [i, s] of spec.slides.entries()) {
    const slide = pptx.addSlide();
    slide.background = { color: 'FFFFFF' };
    if (s.kind === 'title') {
      addText(slide, s.title, { x: 0.7, y: 1.4, w: 11.9, h: 2.5 }, { fontSize: 50, bold: true, color: '17365D' });
      addText(slide, s.subtitle, { x: 0.7, y: 4.25, w: 11.9, h: 1.4 }, { fontSize: 24 });
    } else {
      addText(slide, s.title, { x: 0.7, y: 0.35, w: 11.9, h: 0.8 }, { fontSize: 35, bold: true, color: '17365D' });
      if (s.kind === 'bullets') {
        s.bullets.forEach((bullet, j) => addText(slide, bullet,
          { x: 0.95, y: 1.5 + j * 1.22, w: 11.4, h: 1.08 },
          { fontSize: 20, bullet: { indent: 18 }, hanging: 18, paraSpaceAfterPt: 12 }));
      } else {
        addText(slide, s.caption, { x: 0.7, y: 1.3, w: 11.9, h: 0.8 }, { fontSize: 18 });
        const image = await imageData(path.dirname(specPath), s.image.path);
        const scale = Math.min(11.9 / image.width, 4.5 / image.height);
        const w = image.width * scale, h = image.height * scale;
        const box = { x: 0.7 + (11.9 - w) / 2, y: 2.2 + (4.5 - h) / 2, w, h };
        boxes.push(box);
        slide.addImage({ data: image.data, ...box, altText: `${spec.paper.title}, page ${s.image.page}` });
      }
    }
    addText(slide, `${i + 1}`, { x: 11.9, y: 7, w: 0.65, h: 0.24 }, { fontSize: 12, align: 'right' });
    slide.addNotes(`[Sources]\n${spec.paper.title}; PDF pages ${[...new Set(s.pages)].join(', ')}\n[/Sources]`);
  }
  // Buffer first; exclusive final creation never clobbers an existing deliverable.
  const buffer = await pptx.write({ outputType: 'nodebuffer', compression: true });
  const output = await fs.open(outputPath, 'wx', 0o600);
  try { await output.writeFile(buffer); } finally { await output.close(); }
  return { output: path.resolve(outputPath), slides: spec.slides.length, ratio: '16:9',
    geometry: 'fixed disjoint layout; conservative text limits; visual QA still required', objects: boxes.length };
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  if (process.argv.length !== 4) {
    console.error('Usage: node build.mjs SPEC.json FINAL.pptx'); process.exitCode = 2;
  } else {
    try { console.log(JSON.stringify(await build(path.resolve(process.argv[2]), process.argv[3]))); }
    catch (error) { console.error(error.message); process.exitCode = 1; }
  }
}
