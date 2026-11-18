import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {clamp, ease} from '../edit/anim';
import {
  Caption,
  Counter,
  DANGER,
  Floor,
  Gate,
  Icon,
  Link,
  IconName,
  Node,
  NodeState,
  Pt,
  StateKey,
  Traveller,
  pathPoint,
  stateAt,
} from './Diagram';
import {bez, ClipArt} from './styles/shared';

const RATE = 2.6;
const HOLD = 0.5;
const FPS = 30;

const LINES: [string, string][] = [
  ['3a', 'So where does the video go?'],
  ['3b', 'Straight into storage, through a short-lived upload link. Big files never pass through the website.'],
  ['3c', 'The website just puts the job in a queue, and moves on.'],
  ['3d', "It's asynchronous. Nothing waits on the GPU, so each side can scale, or fail, on its own."],
  ['4', 'Before a GPU gets involved, a small function checks the file.'],
  ['4', 'It measures the real length, instead of trusting the browser, and confirms the price.'],
  ['4', 'A bad file never wakes a GPU.'],
  ['5a', "And the GPU only runs when there's work."],
  ['5a', 'A job arrives, and it scales up.'],
  ['5b', 'Things go quiet, and it scales back to zero.'],
  ['5c', 'The website and the database are serverless, so there’s no server to look after.'],
  ['5c', 'They grow with traffic, and cost almost nothing when idle.'],
  ['5d', "The trade-off is a few minutes' wait while the GPU starts."],
  ['5d', "Nobody needs this result in a second, so it's a trade worth making."],
  ['6', "On the GPU, Meta's TRIBE model, trained on real brain scans, watches the video twice."],
  ['6', "Once with sound, once on mute. You'll see why."],
  ['7a', "And when something goes wrong, you don't pay for it."],
  ['7a', "You're only charged once the result is saved. If the job fails, the hold goes back."],
  ['7b', "Billing is idempotent. If the same job arrives twice, it's only charged once."],
  ['7c', 'And a watchdog makes sure no GPU is left running.'],
  ['7d', 'All of this is infrastructure as code, in Terraform.'],
  ['7e', 'So the whole system can be reviewed, rebuilt, or torn down from code.'],
  ['7f', 'Every part gets least privilege. It can only do its own job.'],
];

let cursor = 0;
export const TIMING = LINES.map(([row, text]) => {
  const frames = Math.round((text.trim().split(/\s+/).length / RATE + HOLD) * FPS);
  const start = cursor;
  cursor += frames;
  return {row, text, start, end: cursor, frames};
});
export const DURATION = cursor;

const CUE = (row: string, occurrence = 0) => {
  const matches = TIMING.filter((line) => line.row === row);
  return matches[occurrence] ?? matches[0];
};

const P = {
  browser: {x: 280, y: 720},
  website: {x: 690, y: 720},
  storage: {x: 900, y: 300},
  queue: {x: 1110, y: 720},
  check: {x: 1510, y: 720},
  gpu: {x: 1970, y: 720},
  database: {x: 690, y: 1090},
  watchdog: {x: 1970, y: 300},
  dead: {x: 2380, y: 1060},
};

type Camera = {cx: number; cy: number; scale: number};
type Box = {x1: number; y1: number; x2: number; y2: number};
type FocusOptions = {primary?: Pt; width?: number; height?: number; maxScale?: number; bias?: number; lift?: number};
type Shot = {at: number; cam: Camera; box: Box; move?: number};
type CameraState = {cam: Camera; fromBox: Box; toBox: Box; t: number};

const SAFE = 80;
const SAFE_FADE = 40;

const camera = (cx: number, cy: number, scale: number): Camera => ({cx, cy, scale});

const nodeBox = (p: Pt): Box => ({x1: p.x - 190, y1: p.y - 84, x2: p.x + 190, y2: p.y + 200});
const propBox = (left: number, top: number, width: number, height: number): Box => ({
  x1: left,
  y1: top,
  x2: left + width,
  y2: top + height,
});
const unionBox = (...boxes: Box[]): Box => ({
  x1: Math.min(...boxes.map((b) => b.x1)),
  y1: Math.min(...boxes.map((b) => b.y1)),
  x2: Math.max(...boxes.map((b) => b.x2)),
  y2: Math.max(...boxes.map((b) => b.y2)),
});

const focus = (boxes: Box[], options: FocusOptions = {}): Camera => {
  const area = unionBox(...boxes);
  const w = Math.max(1, area.x2 - area.x1);
  const h = Math.max(1, area.y2 - area.y1);
  const scale = Math.min(
    (1920 * (options.width ?? 0.6)) / w,
    (1080 * (options.height ?? 0.76)) / h,
    options.maxScale ?? 1.48,
  );
  const bx = (area.x1 + area.x2) / 2;
  const by = (area.y1 + area.y2) / 2;
  const px = options.primary?.x ?? bx;
  const bias = options.bias ?? 0.6;
  const lift = options.lift ?? 0;
  return camera(bx + (px - bx) * bias, by + lift / scale, scale);
};

const COUNTER = {x: P.gpu.x + 150, y: P.gpu.y - 118};
const READOUT = {left: 1180, top: 920, width: 760, height: 108};
const CLOCK = {left: 1560, top: 460, width: 660, height: 220};
const JOBS = {left: 1740, top: 995, width: 1020, height: 140};

const JOB_LANE_Y = 1055;
const FIRST_JOB_X = 1840;
const SECOND_JOB_START = 3200;
const SECOND_JOB_HIT = 2560;
const SECOND_JOB_BOUNCE = 2720;
const STAMP = {x: 2320, y: 1055, width: 280, height: 120};

const makeShot = (at: number, boxes: Box[], options: FocusOptions = {}, move?: number): Shot => ({
  at,
  cam: focus(boxes, options),
  box: unionBox(...boxes),
  ...(move === undefined ? {} : {move}),
});

// The whole system, as large as the Terraform outline and caption allow.
const WIDE: FocusOptions = {width: 0.78, height: 0.7, lift: -30};

const CAMS: Shot[] = [
  makeShot(0, [nodeBox(P.browser), nodeBox(P.website)], {primary: P.website, bias: 0.55}),
  makeShot(CUE('3b').start, [nodeBox(P.browser), nodeBox(P.storage), nodeBox(P.website)], {
    primary: P.storage,
    bias: 0.55,
  }),
  makeShot(CUE('3c').start, [nodeBox(P.website), nodeBox(P.queue)], {primary: P.queue}),
  makeShot(CUE('4').start, [nodeBox(P.queue), nodeBox(P.check)], {primary: P.check}),
  makeShot(
    CUE('4', 1).start,
    [nodeBox(P.queue), nodeBox(P.check), propBox(READOUT.left, READOUT.top, READOUT.width, READOUT.height)],
    {primary: P.check},
  ),
  makeShot(
    CUE('4', 2).start,
    [nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu), propBox(READOUT.left, READOUT.top, READOUT.width, READOUT.height)],
    {primary: P.check},
  ),
  makeShot(
    CUE('5a').start,
    [nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu), propBox(COUNTER.x - 170, COUNTER.y - 60, 340, 120)],
    {primary: P.gpu},
  ),
  makeShot(CUE('5c').start, [nodeBox(P.website), nodeBox(P.database)], {primary: {x: P.website.x, y: 905}}),
  makeShot(
    CUE('5d').start,
    [nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu), propBox(CLOCK.left, CLOCK.top, CLOCK.width, CLOCK.height)],
    {primary: {x: 1900, y: 600}},
  ),
  {at: CUE('6').start + 56, cam: camera(1970, 720, 1.9), box: nodeBox(P.gpu), move: 56},
  makeShot(CUE('7a').start, [nodeBox(P.storage), nodeBox(P.queue), nodeBox(P.check), nodeBox(P.gpu)], {primary: P.gpu}, 80),
  makeShot(
    CUE('7b').start,
    [
      nodeBox(P.gpu),
      propBox(JOBS.left, JOBS.top, JOBS.width, JOBS.height),
      propBox(STAMP.x - STAMP.width / 2, STAMP.y - STAMP.height / 2, STAMP.width, STAMP.height),
    ],
    {primary: {x: 2050, y: 900}},
    36,
  ),
  makeShot(
    CUE('7c').start,
    [nodeBox(P.check), nodeBox(P.gpu), nodeBox(P.watchdog), nodeBox(P.dead)],
    {primary: P.gpu, bias: 0.4},
    36,
  ),
  makeShot(CUE('7d').start, Object.values(P).map(nodeBox), WIDE, 40),
  makeShot(CUE('7e').start, Object.values(P).map(nodeBox), WIDE),
  makeShot(CUE('7f').start, Object.values(P).map(nodeBox), WIDE),
];

const cameraAt = (f: number): CameraState => {
  let current = CAMS[0];
  for (let i = 1; i < CAMS.length; i += 1) {
    const next = CAMS[i];
    if (f >= next.at) {
      current = next;
      continue;
    }
    const move = next.move ?? 26;
    if (f > next.at - move) {
      const t = interpolate(f, [next.at - move, next.at], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
        easing: (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2),
      });
      return {
        cam: {
          cx: current.cam.cx + (next.cam.cx - current.cam.cx) * t,
          cy: current.cam.cy + (next.cam.cy - current.cam.cy) * t,
          scale: current.cam.scale + (next.cam.scale - current.cam.scale) * t,
        },
        fromBox: current.box,
        toBox: next.box,
        t,
      };
    }
    return {cam: current.cam, fromBox: current.box, toBox: current.box, t: 1};
  }
  return {cam: current.cam, fromBox: current.box, toBox: current.box, t: 1};
};

const worldStyle = (cam: Camera): React.CSSProperties => ({
  transform: `translate(${960 - cam.cx * cam.scale}px, ${540 - cam.cy * cam.scale}px) scale(${cam.scale})`,
  transformOrigin: '0 0',
});

const nodeState = (f: number, keys: StateKey[]) => {
  const s = stateAt(f, keys);
  return {state: s.state, stateAt: s.at, prev: s.prev};
};

const glowOf = (f: number, keys: StateKey[]) => {
  const value: Record<NodeState, number> = {hidden: 0, idle: 0.2, lit: 1, dim: 0.06, off: 0};
  const s = stateAt(f, keys);
  const from = s.prev ? value[s.prev] : value[s.state];
  const t = s.prev ? interpolate(f, [s.at, s.at + 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  return from + (value[s.state] - from) * t;
};

const BROWSER: StateKey[] = [
  {at: 6, state: 'idle'},
  {at: CUE('3b').start + 6, state: 'lit'},
  {at: CUE('3b').start + 108, state: 'idle'},
  {at: CUE('3c').start + 8, state: 'dim'},
];

const WEBSITE: StateKey[] = [
  {at: 2, state: 'lit'},
  {at: CUE('3b').start + 16, state: 'idle'},
  {at: CUE('3c').start + 4, state: 'lit'},
  {at: CUE('3c').start + 74, state: 'dim'},
  {at: CUE('5c').start + 6, state: 'lit'},
  {at: CUE('5c').start + 66, state: 'idle'},
  {at: CUE('5c', 1).start + 4, state: 'lit'},
  {at: CUE('5c', 1).start + 62, state: 'dim'},
];

const STORAGE: StateKey[] = [
  {at: CUE('3b').start, state: 'idle'},
  {at: CUE('3b').start + 22, state: 'lit'},
  {at: CUE('3b').start + 112, state: 'idle'},
  {at: CUE('7a', 1).start + 24, state: 'lit'},
  {at: CUE('7b').start - 12, state: 'idle'},
];

const QUEUE: StateKey[] = [
  {at: CUE('3c').start, state: 'idle'},
  {at: CUE('3c').start + 26, state: 'lit'},
  {at: CUE('3c').start + 116, state: 'idle'},
  {at: CUE('4').start + 8, state: 'lit'},
  {at: CUE('4').start + 96, state: 'idle'},
];

const CHECK: StateKey[] = [
  {at: CUE('4').start, state: 'idle'},
  {at: CUE('4').start + 34, state: 'lit'},
  {at: CUE('4', 2).start + 78, state: 'dim'},
];

const GPU: StateKey[] = [
  {at: CUE('5a').start, state: 'idle'},
  {at: CUE('5a', 1).start, state: 'lit'},
  {at: CUE('5b').start, state: 'off'},
  {at: CUE('5d').start, state: 'idle'},
  {at: CUE('6').start, state: 'lit'},
  {at: CUE('7a', 1).start, state: 'off'},
  {at: CUE('7c').start, state: 'lit'},
  {at: CUE('7c').start + 54, state: 'off'},
];

const DATABASE: StateKey[] = [
  {at: CUE('5c').start, state: 'idle'},
  {at: CUE('5c').start + 38, state: 'lit'},
  {at: CUE('5c').start + 108, state: 'idle'},
  {at: CUE('5c', 1).start + 4, state: 'lit'},
  {at: CUE('5c', 1).start + 64, state: 'dim'},
];

const WATCHDOG: StateKey[] = [
  {at: CUE('7c').start, state: 'idle'},
  {at: CUE('7c').start + 26, state: 'lit'},
  {at: CUE('7c').start + 104, state: 'idle'},
];

const DEAD: StateKey[] = [
  {at: CUE('7c').start, state: 'idle'},
  {at: CUE('7c').start + 50, state: 'lit'},
  {at: CUE('7c').start + 112, state: 'idle'},
];

const KEY_BASE = CUE('7f').start;
const WIDE_LEAD = 40;

type NodeDef = {
  id: string;
  x: number;
  y: number;
  label: string;
  service: string;
  purpose: string;
  icon: IconName;
  keys: StateKey[];
  appear: number;
  keyAt: number;
  keySide?: 'left' | 'right';
  dxKey?: 'website' | 'queue';
};

const NODES: NodeDef[] = [
  {id: 'browser', x: P.browser.x, y: P.browser.y, label: 'Browser', service: '', purpose: 'your video', icon: 'browser', keys: BROWSER, appear: 6, keyAt: KEY_BASE},
  {id: 'website', x: P.website.x, y: P.website.y, label: 'Website', service: 'Lambda', purpose: 'sign-in & upload', icon: 'website', keys: WEBSITE, appear: 2, keyAt: KEY_BASE + 7, dxKey: 'website'},
  {id: 'storage', x: P.storage.x, y: P.storage.y, label: 'Storage', service: 'S3', purpose: 'videos & results', icon: 'storage', keys: STORAGE, appear: CUE('3b').start, keyAt: KEY_BASE + 14},
  {id: 'queue', x: P.queue.x, y: P.queue.y, label: 'Queue', service: 'SQS', purpose: 'jobs wait here', icon: 'queue', keys: QUEUE, appear: CUE('3c').start, keyAt: KEY_BASE + 21, dxKey: 'queue'},
  {id: 'check', x: P.check.x, y: P.check.y, label: 'Check', service: 'Lambda', purpose: 'checks length & price', icon: 'check', keys: CHECK, appear: CUE('4').start, keyAt: KEY_BASE + 28},
  {id: 'gpu', x: P.gpu.x, y: P.gpu.y, label: 'GPU', service: 'SageMaker', purpose: 'runs the brain model', icon: 'gpu', keys: GPU, appear: CUE('5a').start, keyAt: KEY_BASE + 35, keySide: 'left'},
  {id: 'database', x: P.database.x, y: P.database.y, label: 'Database', service: 'Aurora Serverless', purpose: 'credits & job records', icon: 'database', keys: DATABASE, appear: CUE('5c').start, keyAt: KEY_BASE + 42},
  {id: 'watchdog', x: P.watchdog.x, y: P.watchdog.y, label: 'Watchdog', service: 'Lambda', purpose: 'stops stuck jobs', icon: 'watchdog', keys: WATCHDOG, appear: CUE('7c').start, keyAt: KEY_BASE + 49},
  {id: 'dead', x: P.dead.x, y: P.dead.y, label: 'Dead-letter', service: 'SQS', purpose: 'failed jobs, refunded', icon: 'deadLetter', keys: DEAD, appear: CUE('7c').start, keyAt: KEY_BASE + 56},
];

let textCtx: CanvasRenderingContext2D | null = null;

const textWidth = (text: string, size: number, weight: number, spacing = 0) => {
  if (typeof document === 'undefined') return text.length * size * 0.62;
  if (!textCtx) textCtx = document.createElement('canvas').getContext('2d');
  if (!textCtx) return text.length * size * 0.62;
  textCtx.font = `${weight} ${size}px ${fontFamily}`;
  return textCtx.measureText(text).width + spacing * size * text.length + 4;
};

const nodeWorldBox = (
  def: NodeDef,
  x: number,
  wide: boolean,
  serviceShown: boolean,
  keyShown: boolean,
): Box => {
  const labelSize = wide ? 64 : 58;
  const labelW = textWidth(def.label, labelSize, wide ? 700 : 600);
  const serviceW = serviceShown ? textWidth(def.service, 42, 400, 0.04) : 0;
  const purposeW = serviceShown ? textWidth(def.purpose, 34, 400) : 0;
  const half = Math.max(64, labelW / 2, serviceW / 2, purposeW / 2, keyShown ? 83 : 0);
  return {
    x1: x - half,
    y1: def.y - 64 - (keyShown ? 8 : 0),
    x2: x + half,
    y2:
      def.y - 64 + 128 + 16 + labelSize +
      (serviceShown && def.service ? 12 + 42 : 0) +
      (serviceShown && def.purpose ? 10 + 34 : 0),
  };
};

const screenBox = (box: Box, cam: Camera): Box => ({
  x1: 960 + (box.x1 - cam.cx) * cam.scale,
  y1: 540 + (box.y1 - cam.cy) * cam.scale,
  x2: 960 + (box.x2 - cam.cx) * cam.scale,
  y2: 540 + (box.y2 - cam.cy) * cam.scale,
});

const safeOpacity = (box: Box) => {
  const inset = Math.min(box.x1 - SAFE, 1920 - SAFE - box.x2, box.y1 - SAFE, 1080 - SAFE - box.y2);
  if (inset <= 0) return 0;
  return clamp(inset / SAFE_FADE);
};

const insideBox = (box: Box, p: Pt) => p.x >= box.x1 && p.x <= box.x2 && p.y >= box.y1 && p.y <= box.y2;

const shotOpacity = (state: CameraState, p: Pt) => {
  const from = insideBox(state.fromBox, p) ? 1 : 0;
  const to = insideBox(state.toBox, p) ? 1 : 0;
  return clamp(from + (to - from) * state.t);
};

const frameGeometry = (f: number) => {
  const cameraState = cameraAt(f);
  const cam = cameraState.cam;
  const wide = f >= CUE('7d').start - WIDE_LEAD;
  const serviceOpacity = wide ? 0 : clamp((cam.scale - 1) / 0.12);
  const sep = ease(f, CUE('3d').start + 10, CUE('3d').start + 40);
  const dx: Record<string, number> = {website: -10 * sep, queue: 10 * sep};
  return {cameraState, cam, wide, serviceOpacity, dx};
};

const nodeX = (def: NodeDef, dx: Record<string, number>) => def.x + (def.dxKey ? dx[def.dxKey] : 0);

export const STILLS = [
  {row: '3a', frame: 18},
  {row: '3b', frame: 185},
  {row: '3c', frame: 355},
  {row: '3d', frame: 609},
  {row: '4', frame: 1027},
  {row: '5a', frame: 1217},
  {row: '5b', frame: 1349},
  {row: '5c', frame: 1420},
  {row: '5d', frame: 1829},
  {row: '6', frame: 2200},
  {row: '7a', frame: 2481},
  {row: '7b', frame: 2740},
  {row: '7c', frame: 2860},
  {row: '7d', frame: 2957},
  {row: '7e', frame: 3043},
  {row: '7f', frame: 3269},
];

export const runSafeAreaCheck = () => {
  let failed = 0;
  for (const still of STILLS) {
    const f = still.frame;
    const {cameraState, cam, wide, serviceOpacity, dx} = frameGeometry(f);
    let count = 0;
    let bad = 0;
    for (const def of NODES) {
      const state = wide ? 'idle' : stateAt(f, def.keys).state;
      if (state === 'hidden' || f < def.appear) continue;
      const x = nodeX(def, dx);
      const box = screenBox(
        nodeWorldBox(def, x, wide, serviceOpacity > 0.01, f >= def.keyAt),
        cam,
      );
      if (shotOpacity(cameraState, {x, y: def.y}) <= 0.02) continue;
      count += 1;
      if (box.x1 < SAFE || box.x2 > 1920 - SAFE || box.y1 < SAFE || box.y2 > 1080 - SAFE) {
        bad += 1;
        console.warn(
          `[v6 safe-area] ${still.row} frame ${f}: ${def.label} box ${Math.round(box.x1)},${Math.round(box.y1)} - ${Math.round(box.x2)},${Math.round(box.y2)} crosses the ${SAFE}px safe area`,
        );
      }
    }
    if (bad > 0) failed += 1;
    else console.info(`[v6 safe-area] ${still.row} frame ${f}: OK (${count} visible nodes)`);
  }
  if (failed > 0) console.warn(`[v6 safe-area] ${failed}/${STILLS.length} stills failed`);
  else console.info(`[v6 safe-area] ${STILLS.length}/${STILLS.length} stills OK`);
};

let checkQueued = false;
const queueSafeAreaCheck = () => {
  if (checkQueued || typeof window === 'undefined') return;
  checkQueued = true;
  const run = () => runSafeAreaCheck();
  if (document.fonts?.ready) document.fonts.ready.then(run).catch(run);
  else run();
};

const Readout: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('4', 1).start - 8, CUE('4', 1).start + 4) *
    (1 - ease(f, CUE('5a').start - 6, CUE('5a').start + 6));
  if (opacity <= 0.001) return null;
  const length = ease(f, CUE('4', 1).start, CUE('4', 1).start + 10);
  const price = ease(f, CUE('4', 1).start + 64, CUE('4', 1).start + 76);
  return (
    <div
      style={{
        position: 'absolute',
        left: READOUT.left,
        top: READOUT.top,
        width: READOUT.width,
        height: READOUT.height,
        display: 'flex',
        alignItems: 'center',
        gap: 34,
        padding: '0 34px',
        boxSizing: 'border-box',
        borderRadius: 26,
        background: 'rgba(13,14,17,.94)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 22px 54px rgba(0,0,0,.52)',
        fontFamily,
        fontSize: 56,
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
        opacity,
      }}
    >
      <span style={{color: C.text, opacity: length, display: 'flex', alignItems: 'center', gap: 14}}>
        length 0:44 <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={56} /></span>
      </span>
      <span style={{color: C.text, opacity: price, display: 'flex', alignItems: 'center', gap: 14}}>
        price <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={56} /></span>
      </span>
    </div>
  );
};

const ClockDial: React.FC<{turn: number}> = ({turn}) => (
  <svg width={52} height={52} viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" style={{display: 'block'}}>
    <circle cx="32" cy="32" r="20" />
    <path d="M32 32l9 6" opacity=".55" />
    <path d="M32 32V18" transform={`rotate(${turn * 270} 32 32)`} />
  </svg>
);

const ClockNote: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('5d').start - 4, CUE('5d').start + 8) *
    (1 - ease(f, CUE('6').start - 8, CUE('6').start + 6));
  if (opacity <= 0.001) return null;
  const tick = ease(f, CUE('5d', 1).start, CUE('5d', 1).start + 12);
  const turn = ease(f, CUE('5d').start, CUE('5d').start + 54);
  return (
    <div
      style={{
        position: 'absolute',
        left: CLOCK.left,
        top: CLOCK.top,
        width: CLOCK.width,
        padding: '26px 30px',
        boxSizing: 'border-box',
        borderRadius: 28,
        background: 'rgba(12,14,18,.95)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 22px 54px rgba(0,0,0,.5)',
        fontFamily,
        fontSize: 46,
        lineHeight: 1.1,
        whiteSpace: 'nowrap',
        color: C.text,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
        opacity,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 16, color: '#FFC08A'}}>
        <ClockDial turn={turn} />
        ~few min to start
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 16, opacity: tick, color: '#C6CCD4'}}>
        <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={44} /></span>
        not needed in a second
      </div>
    </div>
  );
};

const panelOpacity = (f: number) =>
  interpolate(f, [CUE('6').start, CUE('6').start + 24, CUE('6', 1).start + 36, CUE('6', 1).start + 70], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

const GpuPanel: React.FC<{f: number}> = ({f}) => {
  const opacity = panelOpacity(f);
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: P.gpu.x - 360,
        top: P.gpu.y - 270,
        width: 720,
        height: 540,
        boxSizing: 'border-box',
        padding: '26px 34px 28px',
        borderRadius: 46,
        background: 'rgba(9,11,14,.97)',
        border: '2px solid rgba(255,138,42,.6)',
        boxShadow: '0 0 90px rgba(255,138,42,.24), 0 38px 90px rgba(0,0,0,.75)',
        fontFamily,
        opacity,
        transform: `scale(${0.96 + 0.04 * opacity})`,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18, color: '#FFC08A'}}>
        <Icon name="model" size={50} />
        <div style={{fontSize: 52, fontWeight: 600, letterSpacing: '-.03em', color: C.text}}>TRIBE (Meta)</div>
      </div>
      <div style={{display: 'flex', justifyContent: 'center', gap: 34, marginTop: 16}}>
        {[
          {icon: 'speaker' as const, label: 'sound on'},
          {icon: 'muted' as const, label: 'muted'},
        ].map((copy) => (
          <div key={copy.label} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14}}>
            <ClipArt width={190} radius={18} border="1.5px solid rgba(255,255,255,.24)" />
            <div style={{display: 'flex', alignItems: 'center', gap: 12, fontSize: 36, fontWeight: 500, color: '#C6CCD4'}}>
              <span style={{color: C.hot, display: 'inline-flex'}}><Icon name={copy.icon} size={36} /></span>
              {copy.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const CreditBar: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('7a').start - 2, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 18, CUE('7b').start + 4));
  if (opacity <= 0.001) return null;
  const returned = ease(f, CUE('7a', 1).start + 20, CUE('7a', 1).start + 84);
  const barW = 600;
  return (
    <div
      style={{
        position: 'absolute',
        left: 70,
        top: 760,
        width: 690,
        height: 248,
        boxSizing: 'border-box',
        padding: '26px 34px',
        borderRadius: 30,
        background: 'rgba(10,12,16,.96)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 26px 64px rgba(0,0,0,.58)',
        fontFamily,
        opacity,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
        <div style={{fontSize: 48, fontWeight: 600, letterSpacing: '-.02em', color: C.text}}>credit balance</div>
        <div
          style={{
            fontSize: 48,
            fontWeight: 600,
            color: C.hot,
            opacity: 1 - returned,
            letterSpacing: '.01em',
          }}
        >
          held
        </div>
      </div>
      <div style={{position: 'relative', marginTop: 30, width: barW, height: 28, borderRadius: 14, background: '#232830', overflow: 'hidden'}}>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: 14,
            background: 'linear-gradient(90deg, rgba(255,138,42,.45), rgba(255,176,110,.9))',
            opacity: 0.72 + 0.28 * returned,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: barW - 150,
            top: 0,
            width: 150,
            height: 28,
            background:
              'repeating-linear-gradient(135deg, rgba(9,11,14,.72) 0 8px, rgba(255,138,42,.9) 8px 15px)',
            opacity: 1 - ease(f, CUE('7a', 1).start + 104, CUE('7a', 1).start + 120),
            transform: `translateX(${-returned * (barW - 150)}px)`,
          }}
        />
      </div>
      <div style={{marginTop: 16, height: 42, fontSize: 44, color: '#FFC08A', opacity: returned, display: 'flex', alignItems: 'center', gap: 12}}>
        <Icon name="tick" size={36} /> hold returned
      </div>
    </div>
  );
};

const DuplicateJob: React.FC<{f: number}> = ({f}) => {
  const start = CUE('7b').start;
  const opacity = ease(f, start + 10, start + 24) * (1 - ease(f, CUE('7c').start - 10, CUE('7c').start + 6));
  if (opacity <= 0.001) return null;
  const first = ease(f, start + 24, start + 54);
  const accepted = ease(f, start + 52, start + 62);
  const second = ease(f, start + 66, start + 98);
  const bounce = ease(f, start + 98, start + 124);
  const firstX = 1200 + (FIRST_JOB_X - 1200) * first;
  const secondX = SECOND_JOB_START + (SECOND_JOB_HIT - SECOND_JOB_START) * second + (SECOND_JOB_BOUNCE - SECOND_JOB_HIT) * bounce;
  const secondOpacity = 1 - ease(f, start + 118, start + 138);
  const stamp = ease(f, start + 52, start + 68);
  const stampPulse =
    1 +
    0.06 *
      ease(f, start + 52, start + 58) *
      (1 - ease(f, start + 58, start + 74));
  const jobCard = (left: number, alpha: number) => (
    <div
      style={{
        position: 'absolute',
        left: left - 100,
        top: JOB_LANE_Y - 50,
        width: 200,
        height: 100,
        borderRadius: 24,
        background: '#1C1E22',
        border: '1.5px solid rgba(255,158,84,.55)',
        boxShadow: '0 16px 34px rgba(0,0,0,.62), 0 0 26px rgba(255,138,42,.18)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 14,
        fontFamily,
        fontSize: 44,
        fontWeight: 500,
        color: C.text,
        opacity: alpha,
      }}
    >
      <div style={{width: 13, height: 13, borderRadius: 7, background: C.hot}} />
      job
    </div>
  );
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      {jobCard(firstX, 1)}
      {jobCard(secondX, secondOpacity)}
      <div
        style={{
          position: 'absolute',
          left: FIRST_JOB_X + 100,
          top: JOB_LANE_Y - 22,
          width: 44,
          height: 44,
          borderRadius: 22,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(12,16,12,.94)',
          border: '2px solid rgba(255,158,84,.8)',
          color: C.hot,
          opacity: accepted,
          transform: `scale(${0.8 + 0.2 * accepted})`,
        }}
      >
        <Icon name="tick" size={30} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: STAMP.x - STAMP.width / 2,
          top: STAMP.y - STAMP.height / 2,
          width: STAMP.width,
          height: STAMP.height,
          boxSizing: 'border-box',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: '3.5px solid rgba(255,138,42,.85)',
          borderRadius: 20,
          color: '#FFC08A',
          fontFamily,
          fontSize: 54,
          fontWeight: 700,
          letterSpacing: '-.01em',
          transform: `rotate(-7deg) scale(${stampPulse})`,
          opacity: stamp,
          background: 'rgba(18,12,8,.72)',
          boxShadow: '0 0 38px rgba(255,138,42,.25)',
          textAlign: 'center',
          lineHeight: 1,
        }}
      >
        Charged
        <br />
        once
      </div>
    </div>
  );
};

const glide = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const THUMB_START: Pt = {x: P.browser.x - 150, y: P.browser.y - 100};
const THUMB_STOPS = {
  website: {from: 40, to: 72, at: {x: P.website.x - 150, y: P.website.y - 100}},
  storage: {
    from: CUE('3b').start + 18,
    to: CUE('3b').start + 66,
    at: {x: P.storage.x - 150, y: P.storage.y},
    control: {x: 700, y: 480},
  },
  queue: {
    from: CUE('3c').start + 18,
    to: CUE('3c').start + 66,
    at: {x: P.queue.x - 150, y: P.queue.y - 100},
    control: {x: 1050, y: 420},
  },
  check: {from: CUE('4').start + 18, to: CUE('4').start + 66, at: {x: P.check.x - 150, y: P.check.y - 100}},
  gpu: {
    from: CUE('5a', 1).start + 4,
    to: CUE('5a', 1).start + 52,
    at: {x: P.gpu.x - 220, y: P.gpu.y + 80},
    control: {x: 1600, y: 500},
  },
  result: {
    from: CUE('7a', 1).start + 12,
    to: CUE('7a', 1).start + 64,
    at: {x: P.storage.x - 150, y: P.storage.y},
    control: {x: 1450, y: 330},
  },
};

type ThumbMove = {from: number; to: number; at: Pt; control?: Pt};

const THUMB_MOVES: ThumbMove[] = [
  THUMB_STOPS.website,
  THUMB_STOPS.storage,
  THUMB_STOPS.queue,
  THUMB_STOPS.check,
  THUMB_STOPS.gpu,
  THUMB_STOPS.result,
];

const thumbAt = (f: number): Pt => {
  let at = THUMB_START;
  for (const move of THUMB_MOVES) {
    if (f <= move.from) return at;
    const t = glide(f, move.from, move.to);
    if (f < move.to) {
      if (move.control) return bez(at, move.control, move.at, t);
      return {x: at.x + (move.at.x - at.x) * t, y: at.y + (move.at.y - at.y) * t};
    }
    at = move.at;
  }
  return at;
};

const JobThumb: React.FC<{f: number}> = ({f}) => {
  const at = thumbAt(f);
  const opacity = glide(f, 10, 22);
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: at.x - 46,
        top: at.y - 82,
        width: 92,
        opacity,
        pointerEvents: 'none',
      }}
    >
      <ClipArt
        width={92}
        radius={14}
        border="1.5px solid rgba(255,255,255,.72)"
        style={{
          boxShadow: '0 0 0 1px rgba(255,138,42,.4), 0 18px 40px rgba(0,0,0,.78), 0 0 42px rgba(255,138,42,.3)',
        }}
      />
    </div>
  );
};

const IdleCost: React.FC<{f: number}> = ({f}) => {
  const opacity =
    ease(f, CUE('5b').start + 36, CUE('5b').start + 48) *
    (1 - ease(f, CUE('5c').start - 14, CUE('5c').start - 2));
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: P.gpu.x,
        top: P.gpu.y + 300,
        transform: 'translate(-50%, -50%)',
        fontFamily,
        fontSize: 38,
        fontWeight: 600,
        letterSpacing: '.005em',
        color: C.text,
        background: 'rgba(8,9,11,.8)',
        border: `1px solid ${C.rule}`,
        borderRadius: 999,
        padding: '12px 26px 14px',
        whiteSpace: 'nowrap',
        boxShadow: '0 14px 40px rgba(0,0,0,.5)',
        opacity,
      }}
    >
      <span style={{color: C.hot}}>$0</span> when idle
    </div>
  );
};

const TerraformOutline: React.FC<{f: number}> = ({f}) => {
  const draw = ease(f, CUE('7d').start + 6, CUE('7d').start + 42);
  const label = ease(f, CUE('7d').start + 28, CUE('7d').start + 46);
  const revive = f >= CUE('7e').start && f <= CUE('7e').end ? 0.7 : 0;
  if (draw <= 0.001) return null;
  return (
    <>
      <svg
        width={3000}
        height={1600}
        viewBox="0 0 3000 1600"
        style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}
      >
        <rect
          x={100}
          y={200}
          width={2500}
          height={1150}
          rx={64}
          fill="none"
          stroke={revive > 0 ? 'rgba(255,178,122,.86)' : 'rgba(160,176,198,.68)'}
          strokeWidth={3}
          pathLength={1}
          strokeDasharray="1"
          strokeDashoffset={1 - draw}
          filter={revive > 0 ? 'url(#v6-outline-glow)' : undefined}
        />
        <defs>
          <filter id="v6-outline-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
        </defs>
      </svg>
      <div
        style={{
          position: 'absolute',
          left: 160,
          top: 156,
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '12px 24px',
          borderRadius: 999,
          background: 'rgba(10,12,16,.96)',
          border: '1.5px solid rgba(160,176,198,.45)',
          color: '#D6DCE4',
          fontFamily,
          fontSize: 54,
          fontWeight: 600,
          letterSpacing: '-.02em',
          opacity: label,
          transform: `translateY(${(1 - label) * 10}px)`,
          boxShadow: '0 18px 46px rgba(0,0,0,.55)',
        }}
      >
        <span style={{color: '#9FB0C4', display: 'inline-flex'}}><Icon name="terraform" size={44} /></span>
        Terraform
      </div>
    </>
  );
};

export const WalkthroughV6Diagram: React.FC = () => {
  const f = useCurrentFrame();
  queueSafeAreaCheck();
  const {cameraState, cam, wide, serviceOpacity, dx} = frameGeometry(f);
  const panelDim = panelOpacity(f);
  const sep = ease(f, CUE('3d').start + 10, CUE('3d').start + 40);
  const dxW = dx.website;
  const dxQ = dx.queue;

  const browserToWebsite = {from: {x: P.browser.x + 84, y: P.browser.y}, to: {x: P.website.x - 84 + dxW, y: P.website.y}};
  const browserToStorage = {
    from: {x: P.browser.x + 44, y: P.browser.y - 52},
    to: {x: P.storage.x - 64, y: P.storage.y + 34},
    bend: -70,
  };
  const websiteToQueue = {from: {x: P.website.x + 84 + dxW, y: P.website.y}, to: {x: P.queue.x - 84 + dxQ, y: P.queue.y}};
  const queueToCheck = {from: {x: P.queue.x + 84 + dxQ, y: P.queue.y}, to: {x: P.check.x - 84, y: P.check.y}};
  const checkToGpu = {from: {x: P.check.x + 84, y: P.check.y}, to: {x: P.gpu.x - 84, y: P.gpu.y}};
  const websiteToDatabase = {
    from: {x: P.website.x + 100 + dxW, y: P.website.y + 40},
    to: {x: P.database.x, y: P.database.y - 84},
    control: {x: 970, y: 890},
  };
  const gpuToDead = {
    from: {x: P.gpu.x + 110, y: P.gpu.y + 30},
    to: {x: P.dead.x - 84, y: P.dead.y},
    control: {x: P.gpu.x + 330, y: P.dead.y - 110},
  };
  const watchdogToGpu = {
    from: {x: P.watchdog.x + 60, y: P.watchdog.y + 64},
    to: {x: P.gpu.x + 60, y: P.gpu.y - 64},
    control: {x: P.gpu.x + 260, y: 510},
  };
  const forbidden = {
    from: {x: P.queue.x - 80 + dxQ, y: P.queue.y + 58},
    to: {x: P.database.x + 84, y: P.database.y},
    control: {x: 850, y: 920},
  };

  const gateAt = pathPoint(checkToGpu, 0.62);
  const gateOpacity =
    ease(f, CUE('4', 2).start - 18, CUE('4', 2).start - 6) *
    (1 - ease(f, CUE('5a').start, CUE('5a').start + 14));
  const gateClosed =
    ease(f, CUE('4', 2).start + 34, CUE('4', 2).start + 48) *
    (1 - ease(f, CUE('5a', 1).start - 20, CUE('5a', 1).start - 4));
  const gateCross =
    ease(f, CUE('4', 2).start + 56, CUE('4', 2).start + 68) *
    (1 - ease(f, CUE('5a').start, CUE('5a').start + 12));

  const running =
    f < CUE('5a', 1).start
      ? 0
      : f < CUE('5b').start
        ? ease(f, CUE('5a', 1).start + 16, CUE('5a', 1).start + 42)
        : 1 - ease(f, CUE('5b').start + 18, CUE('5b').start + 54);
  const counterOpacity =
    ease(f, CUE('5a').start - 4, CUE('5a').start + 8) *
    (1 - ease(f, CUE('5c').start - 10, CUE('5c').start + 6));

  const sceneOpacity = interpolate(
    f,
    [CUE('7e').start, CUE('7e').start + 34, CUE('7e').start + 60, CUE('7e').start + 122],
    [1, 0.06, 0.06, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const gpuFail =
    ease(f, CUE('7a').start + 6, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 10, CUE('7b').start + 10));

  const linkOpacity = (a: Pt, b: Pt) =>
    Math.min(shotOpacity(cameraState, a), shotOpacity(cameraState, b));

  const pool = (def: NodeDef) => {
    const x = nodeX(def, dx);
    const state = wide ? 'idle' : stateAt(f, def.keys).state;
    const glow = wide ? 0.2 : glowOf(f, def.keys);
    return {
      x,
      y: def.y + 66,
      rx: 200,
      ry: 88,
      opacity: state === 'hidden' ? 0 : shotOpacity(cameraState, {x, y: def.y}) * (0.16 + 0.58 * glow),
      color: 'rgba(255,138,42,.26)',
    };
  };

  return (
    <AbsoluteFill style={{background: 'radial-gradient(1300px 900px at 50% 42%, #0B0E13 0%, #07090C 58%, #040507 100%)'}}>
      <AbsoluteFill style={worldStyle(cam)}>
        <div style={{position: 'absolute', inset: 0, opacity: sceneOpacity}}>
          <div
            style={{
              position: 'absolute',
              inset: 0,
              opacity: 1 - 0.8 * panelDim,
              filter: panelDim > 0.001 ? `blur(${panelDim * 14}px) brightness(${1 - 0.66 * panelDim})` : undefined,
            }}
          >
          <Floor pools={NODES.map(pool)} />

          <svg width={3000} height={1600} viewBox="0 0 3000 1600" style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
            <defs>
              <filter id="v6-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="8" />
              </filter>
              <filter id="v6-floor-blur" x="-70%" y="-70%" width="240%" height="240%">
                <feGaussianBlur stdDeviation="20" />
              </filter>
            </defs>

            <Link
              path={browserToWebsite}
              appear={10}
              draw={18}
              width={1.8}
              colour="rgba(150,166,188,.5)"
              opacity={linkOpacity(browserToWebsite.from, browserToWebsite.to)}
            />
            <Link
              path={browserToStorage}
              appear={CUE('3b').start + 12}
              draw={16}
              arrow
              opacity={linkOpacity(browserToStorage.from, browserToStorage.to)}
              hot={ease(f, CUE('3b').start + 6, CUE('3b').start + 22) * (1 - ease(f, CUE('3b').start + 96, CUE('3b').start + 126))}
            />
            <Link
              path={websiteToQueue}
              appear={CUE('3c').start + 12}
              draw={16}
              arrow
              opacity={linkOpacity(websiteToQueue.from, websiteToQueue.to)}
              dashed={f >= CUE('3d').start + 4}
              dashedAt={CUE('3d').start + 4}
              gap={28 * sep}
            />
            <Link path={queueToCheck} appear={CUE('4').start + 12} draw={16} arrow opacity={linkOpacity(queueToCheck.from, queueToCheck.to)} />
            <Link path={checkToGpu} appear={CUE('4', 2).start - 30} draw={18} arrow opacity={linkOpacity(checkToGpu.from, checkToGpu.to)} />
            <Link path={websiteToDatabase} appear={CUE('5c').start + 12} draw={16} arrow opacity={linkOpacity(websiteToDatabase.from, websiteToDatabase.to)} />
            <Link path={gpuToDead} appear={CUE('7c').start + 14} draw={18} arrow opacity={linkOpacity(gpuToDead.from, gpuToDead.to)} />
            <Link path={watchdogToGpu} appear={CUE('7c').start + 18} draw={20} arrow opacity={linkOpacity(watchdogToGpu.from, watchdogToGpu.to)} />
            <Link
              path={forbidden}
              appear={CUE('7f').start + 6}
              draw={20}
              colour="rgba(160,176,198,.5)"
              opacity={linkOpacity(forbidden.from, forbidden.to)}
              blocked={ease(f, CUE('7f').start + 34, CUE('7f').start + 50)}
              arrow
            />

            {gateOpacity > 0.001 ? (
              <g opacity={gateOpacity}>
                <Gate x={gateAt.x} y={gateAt.y} closed={gateClosed} cross={gateCross} />
              </g>
            ) : null}

            <Traveller
              path={{from: {x: P.queue.x, y: P.queue.y - 250}, to: {x: P.queue.x, y: P.queue.y - 140}}}
              start={CUE('3c').start + 18}
              end={CUE('3c').start + 44}
              kind="job"
              linger
              outAt={CUE('3c').start + 104}
            />
            <Traveller
              path={checkToGpu}
              start={CUE('4', 2).start - 8}
              end={CUE('4', 2).start + 70}
              kind="dot"
              colour={DANGER}
              stopAt={0.55}
              linger
              outAt={CUE('5a').start}
            />
            <Traveller
              path={gpuToDead}
              start={CUE('7c').start + 46}
              end={CUE('7c').start + 96}
              kind="dot"
              colour={DANGER}
              linger
              outAt={CUE('7d').start - 6}
            />

            {gpuFail > 0.001 ? (
              <g opacity={gpuFail}>
                <circle cx={P.gpu.x + 46} cy={P.gpu.y - 46} r={31} fill="#0B0C0F" opacity={0.92} stroke={DANGER} strokeWidth={3} />
                <g stroke={DANGER} strokeWidth={6} strokeLinecap="round">
                  <path d={`M ${P.gpu.x + 34} ${P.gpu.y - 58} L ${P.gpu.x + 58} ${P.gpu.y - 34}`} />
                  <path d={`M ${P.gpu.x + 34} ${P.gpu.y - 34} L ${P.gpu.x + 58} ${P.gpu.y - 58}`} />
                </g>
              </g>
            ) : null}
          </svg>

          <JobThumb f={f} />

          {NODES.map((def) => {
            const x = nodeX(def, dx);
            const serviceShown = serviceOpacity > 0.01;
            const box = nodeWorldBox(def, x, wide, serviceShown, f >= def.keyAt);
            const visible =
              shotOpacity(cameraState, {x, y: def.y}) * safeOpacity(screenBox(box, cam));
            const state = nodeState(f, def.keys);
            const stateProps = wide
              ? {state: 'idle' as NodeState, stateAt: 0, prev: undefined}
              : state;
            return (
              <Node
                key={def.id}
                x={x}
                y={def.y}
                label={def.label}
                service={def.service}
                purpose={def.purpose}
                icon={def.icon}
                {...stateProps}
                appear={def.appear}
                keyAt={def.keyAt}
                keySide={def.keySide}
                wide={wide}
                serviceOpacity={serviceOpacity}
                safe={visible}
              />
            );
          })}

          <Readout f={f} />
          <ClockNote f={f} />
          <IdleCost f={f} />
          <Counter x={P.gpu.x + 150} y={P.gpu.y - 118} value={running} opacity={counterOpacity} />
          <DuplicateJob f={f} />
          </div>
          <GpuPanel f={f} />
        </div>

        <TerraformOutline f={f} />
      </AbsoluteFill>

      <Caption text="asynchronous" from={CUE('3d').start + 8} to={CUE('4').start - 10} hot="asynchronous" />
      <Caption text="scale to zero" from={CUE('5b').start + 4} to={CUE('5c').start - 8} hot="zero" />
      <Caption text="serverless" from={CUE('5c').start + 6} to={CUE('5d').start - 10} hot="serverless" y={985} />
      <Caption text="idempotent" from={CUE('7b').start + 6} to={CUE('7c').start - 10} hot="idempotent" />
      <Caption text="infrastructure as code" from={CUE('7d').start + 6} to={CUE('7e').start - 6} hot="code" />
      <Caption text="least privilege" from={CUE('7f').start + 8} to={CUE('7f').end + 4} hot="privilege" />
      <CreditBar f={f} />
    </AbsoluteFill>
  );
};
