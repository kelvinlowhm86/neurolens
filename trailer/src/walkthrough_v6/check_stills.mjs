// Renders each shot's hold frame (STILLS in Diagram37.tsx) to out/stills/ and measures the
// bounding box of the bright (lit) pixels above the credit strip. Fails if that box is not
// centred in the 1920x970 area above the strip, or touches the strip. The in-render check
// (runFramingCheck) measures the same box from geometry; this one measures the real frame.
// Run from the trailer folder: node src/walkthrough_v6/check_stills.mjs [shot ...]
import {createRequire} from 'node:module';
import {mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import zlib from 'node:zlib';

const root = process.cwd();
const require = createRequire(path.join(root, 'package.json'));
const esbuild = require('esbuild');
const {bundle} = require('@remotion/bundler');
const {renderStill, selectComposition} = require('@remotion/renderer');

const STRIP_TOP = 970;
const CX = 960;
const CY = STRIP_TOP / 2;
const TOLERANCE = 0.04;
const LIT = 110; // a pixel is lit when its brightest channel is at least this
const MARGIN_ABOVE_STRIP = 8;

const decodePng = (buffer) => {
  let pos = 8;
  let width = 0;
  let height = 0;
  let channels = 0;
  const idat = [];
  while (pos < buffer.length) {
    const length = buffer.readUInt32BE(pos);
    const type = buffer.toString('ascii', pos + 4, pos + 8);
    const data = buffer.subarray(pos + 8, pos + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      if (data[8] !== 8 || data[12] !== 0) throw new Error('only 8-bit non-interlaced PNG is supported');
      channels = {2: 3, 6: 4}[data[9]];
      if (!channels) throw new Error(`unsupported PNG colour type ${data[9]}`);
    } else if (type === 'IDAT') idat.push(data);
    pos += 12 + length;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x += 1) {
      const left = x >= channels ? out[y * stride + x - channels] : 0;
      const up = y > 0 ? out[(y - 1) * stride + x] : 0;
      const upLeft = y > 0 && x >= channels ? out[(y - 1) * stride + x - channels] : 0;
      let predictor = 0;
      if (filter === 1) predictor = left;
      else if (filter === 2) predictor = up;
      else if (filter === 3) predictor = (left + up) >> 1;
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        predictor = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
      }
      out[y * stride + x] = (line[x] + predictor) & 255;
    }
  }
  return {width, height, channels, data: out};
};

const brightBox = ({width, height, channels, data}) => {
  let x1 = width;
  let y1 = height;
  let x2 = -1;
  let y2 = -1;
  for (let y = 0; y < STRIP_TOP; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * channels;
      if (Math.max(data[i], data[i + 1], data[i + 2]) >= LIT) {
        if (x < x1) x1 = x;
        if (x > x2) x2 = x;
        if (y < y1) y1 = y;
        if (y > y2) y2 = y;
      }
    }
  }
  return {x1, y1, x2, y2};
};

const work = mkdtempSync(path.join(root, 'out', 'v6-check-')); // inside the project so node_modules resolves
let bundleDir;
try {
  // STILLS lives in Diagram37.tsx; build just enough of it to read the frames.
  const built = path.join(work, 'diagram.mjs');
  esbuild.buildSync({
    entryPoints: [path.join(root, 'src/walkthrough_v6/Diagram37.tsx')],
    bundle: true,
    platform: 'node',
    format: 'esm',
    packages: 'external',
    outfile: built,
    logLevel: 'error',
  });
  const {STILLS} = await import(pathToFileURL(built).href);
  const wanted = process.argv.slice(2);
  const shots = STILLS.filter((still) => wanted.length === 0 || wanted.includes(still.row));
  if (shots.length === 0) throw new Error(`no stills match ${wanted.join(', ')}`);

  bundleDir = path.join(work, 'bundle');
  const serveUrl = await bundle({entryPoint: path.join(root, 'src/index.ts'), outDir: bundleDir, publicDir: path.join(root, 'public')});
  const browserExecutable = process.platform === 'darwin' && process.arch === 'arm64' ? path.join(root, 'tools/chrome-local.sh') : undefined;
  const composition = await selectComposition({serveUrl, id: 'WalkthroughV6Diagram', browserExecutable});
  const stillsDir = path.join(root, 'out/stills');
  mkdirSync(stillsDir, {recursive: true});

  let failed = 0;
  for (const shot of shots) {
    const output = path.join(stillsDir, `v6-${shot.row.replace(' pass ', '-pass')}.png`);
    await renderStill({composition, serveUrl, output, frame: shot.frame, browserExecutable, imageFormat: 'png'});
    const box = brightBox(decodePng(readFileSync(output)));
    const cx = (box.x1 + box.x2) / 2;
    const cy = (box.y1 + box.y2) / 2;
    const problems = [];
    if (box.x2 < 0) problems.push('no lit pixels');
    if (Math.abs(cx - CX) > TOLERANCE * 1920) problems.push(`centre x ${Math.round(cx)} is off ${CX} by more than ${TOLERANCE * 100}%`);
    if (Math.abs(cy - CY) > TOLERANCE * STRIP_TOP) problems.push(`centre y ${Math.round(cy)} is off ${CY} by more than ${TOLERANCE * 100}%`);
    if (box.y2 > STRIP_TOP - MARGIN_ABOVE_STRIP) problems.push(`lit pixels reach ${box.y2}, touching the strip at ${STRIP_TOP}`);
    if (problems.length) failed += 1;
    console.log(
      `${problems.length ? 'FAIL' : 'ok  '} ${shot.row.padEnd(10)} frame ${String(shot.frame).padStart(4)} box ${box.x1},${box.y1}-${box.x2},${box.y2} centre ${Math.round(cx)},${Math.round(cy)} ${problems.join('; ')}  ${output}`,
    );
  }
  if (failed > 0) {
    console.error(`${failed} shot(s) failed the lit-pixel framing check`);
    process.exitCode = 1;
  }
} finally {
  rmSync(work, {recursive: true, force: true});
}
