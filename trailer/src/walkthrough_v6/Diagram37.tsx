import React from 'react';
import {AbsoluteFill, Easing, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {clamp, ease} from '../edit/anim';
import {
  Caption,
  DANGER,
  Icon,
  IconName,
  Link,
  Node,
  NodeState,
  PathSpec,
  Pt,
  StateKey,
  stateAt,
} from './Diagram';
import {bez, CLIP_SRC, CLIP_VIDEO_SRC, MONO} from './styles/shared';

const RATE = 2.6;
const HOLD = 0.5;
const FPS = 30;
const SAFE = 80;

const LINES: [string, string][] = [
  [
    '3b',
    "The video goes straight into S3, through a short-lived upload link. It never passes through the website, so a big file can't slow it down for anyone else.",
  ],
  ['3c', "The website, a Lambda function, just puts a job on an SQS queue, and it's done."],
  ['3d', "It's asynchronous. Nothing waits on the GPU, so each side can scale, or fail, on its own."],
  [
    '4',
    'Before any money is spent on a GPU, another Lambda opens the file and reads its real length, rather than trusting what the browser said. It works the price out again from that.',
  ],
  [
    '4r',
    "If the file won't open, runs too long, or there isn't enough credit, you're refunded right there, and no GPU ever wakes up.",
  ],
  [
    '5a',
    'GPUs are expensive, so none is left running. The model runs on SageMaker, which starts a GPU when a job arrives, and scales back to zero when things go quiet.',
  ],
  [
    '5b',
    "The catch is that starting one takes a few minutes. For a chatbot, that would be a problem. But you're not waiting for a reply here; you're getting an analysis to study. So a few minutes is a fair price for paying nothing while nobody's using it.",
  ],
  [
    '5c',
    "The website and the database work the same way. Lambda and Aurora Serverless cost almost nothing when idle, and there's no server to look after.",
  ],
  [
    '6',
    "Once it's up, it runs TRIBE, a research model from Meta. It learned from the brain scans of over seven hundred people as they watched films and listened to podcasts, so it can predict how a brain responds to a video it's never seen.",
  ],
  ['6', "It watches yours twice. Once with sound, once on mute. You'll see why."],
  [
    '7a',
    "And if something breaks halfway? It tries once more. If it fails again, the job goes to a dead-letter queue, and your hold goes straight back. You're only charged once the result is saved.",
  ],
  ['7b', 'SQS can occasionally deliver the same job twice, so billing is idempotent: one job, one charge.'],
  ['7c', "And a watchdog Lambda makes sure a stuck job can't leave a GPU quietly running up a bill."],
  [
    '7d',
    'All of it is written as code, in Terraform, so the whole system can be reviewed, rebuilt, or torn down in one go.',
  ],
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

const beat = (row: string, progress = 0, occurrence = 0) => {
  const cue = CUE(row, occurrence);
  return Math.round(cue.start + (cue.end - cue.start) * progress);
};

type NodeId = 'browser' | 'website' | 'storage' | 'queue' | 'check' | 'gpu' | 'database' | 'watchdog' | 'dead';

type NodeDef = {
  id: NodeId;
  label: string;
  service: string;
  purpose: string;
  icon: IconName;
  keys: StateKey[];
  appear: number;
};

const BROWSER: StateKey[] = [
  {at: 0, state: 'lit'},
  {at: CUE('3c').start, state: 'dim'},
];

const WEBSITE: StateKey[] = [
  {at: 0, state: 'dim'},
  {at: CUE('3c').start, state: 'lit'},
  {at: CUE('3d').start, state: 'dim'},
  {at: CUE('5c').start, state: 'lit'},
  {at: beat('5c', 0.55), state: 'idle'},
];

const STORAGE: StateKey[] = [
  {at: 0, state: 'idle'},
  {at: beat('3b', 0.16), state: 'lit'},
  {at: beat('3b', 0.58), state: 'idle'},
];

const QUEUE: StateKey[] = [
  {at: CUE('3c').start, state: 'idle'},
  {at: beat('3c', 0.22), state: 'lit'},
  {at: beat('3c', 0.62), state: 'idle'},
  {at: CUE('4').start, state: 'lit'},
  {at: beat('4', 0.3), state: 'idle'},
  {at: CUE('5a').start, state: 'lit'},
  {at: beat('5a', 0.28), state: 'idle'},
];

const CHECK: StateKey[] = [
  {at: CUE('4').start, state: 'lit'},
  {at: beat('5a', 0.25), state: 'idle'},
];

const GPU: StateKey[] = [
  {at: CUE('4r').start, state: 'off'},
  {at: beat('5a', 0.2), state: 'lit'},
  {at: beat('5a', 0.72), state: 'off'},
  {at: CUE('5b').start, state: 'off'},
  {at: CUE('6').start, state: 'lit'},
  {at: CUE('7a').start, state: 'lit'},
  {at: beat('7a', 0.3), state: 'off'},
  {at: CUE('7b').start, state: 'lit'},
  {at: beat('7b', 0.72), state: 'idle'},
  {at: CUE('7c').start, state: 'lit'},
  {at: beat('7c', 0.55), state: 'off'},
  {at: CUE('7d').start, state: 'idle'},
];

const DATABASE: StateKey[] = [
  {at: CUE('5c').start, state: 'idle'},
  {at: beat('5c', 0.12), state: 'lit'},
  {at: beat('5c', 0.58), state: 'idle'},
];

const WATCHDOG: StateKey[] = [
  {at: CUE('7c').start, state: 'idle'},
  {at: beat('7c', 0.15), state: 'lit'},
  {at: beat('7c', 0.72), state: 'idle'},
];

const DEAD: StateKey[] = [
  {at: CUE('7a').start, state: 'idle'},
  {at: beat('7a', 0.64), state: 'lit'},
  {at: beat('7a', 0.94), state: 'idle'},
];

const NODES: NodeDef[] = [
  {id: 'browser', label: 'Browser', service: '', purpose: 'your video', icon: 'browser', keys: BROWSER, appear: 0},
  {id: 'website', label: 'Website', service: 'Lambda', purpose: 'sign-in & upload', icon: 'website', keys: WEBSITE, appear: 0},
  {id: 'storage', label: 'Storage', service: 'S3', purpose: 'videos & results', icon: 'storage', keys: STORAGE, appear: 0},
  {id: 'queue', label: 'Queue', service: 'SQS', purpose: 'jobs wait here', icon: 'queue', keys: QUEUE, appear: CUE('3c').start},
  {id: 'check', label: 'Check', service: 'Lambda', purpose: 'checks length & price', icon: 'check', keys: CHECK, appear: CUE('4').start},
  {id: 'gpu', label: 'GPU', service: 'SageMaker', purpose: 'runs the brain model', icon: 'gpu', keys: GPU, appear: CUE('4r').start},
  {id: 'database', label: 'Database', service: 'Aurora Serverless', purpose: 'credits & job records', icon: 'database', keys: DATABASE, appear: CUE('5c').start},
  {id: 'watchdog', label: 'Watchdog', service: 'Lambda', purpose: 'stops stuck jobs', icon: 'watchdog', keys: WATCHDOG, appear: CUE('7c').start},
  {id: 'dead', label: 'Dead-letter', service: 'SQS', purpose: 'failed jobs, refunded', icon: 'deadLetter', keys: DEAD, appear: CUE('7a').start},
];

const NODES_BY_ID = Object.fromEntries(NODES.map((node) => [node.id, node])) as Record<NodeId, NodeDef>;

type Camera = {cx: number; cy: number; scale: number};
type Box = {x1: number; y1: number; x2: number; y2: number};

const camera = (cx: number, cy: number, scale: number): Camera => ({cx, cy, scale});

const unionBox = (...boxes: Box[]): Box => ({
  x1: Math.min(...boxes.map((box) => box.x1)),
  y1: Math.min(...boxes.map((box) => box.y1)),
  x2: Math.max(...boxes.map((box) => box.x2)),
  y2: Math.max(...boxes.map((box) => box.y2)),
});

let textCtx: CanvasRenderingContext2D | null = null;

const textWidth = (text: string, size: number, weight: number, spacing = 0) => {
  if (!text) return 0;
  if (typeof document === 'undefined') return text.length * size * 0.62;
  if (!textCtx) textCtx = document.createElement('canvas').getContext('2d');
  if (!textCtx) return text.length * size * 0.62;
  textCtx.font = `${weight} ${size}px ${fontFamily}`;
  return textCtx.measureText(text).width + spacing * size * text.length + 4;
};

const nodeWorldBox = (
  def: NodeDef,
  x: number,
  y: number,
  wide: boolean,
  serviceShown: boolean,
  purposeShown: boolean,
): Box => {
  const labelSize = 58;
  const labelW = textWidth(def.label, labelSize, 600);
  const serviceW = serviceShown ? textWidth(def.service, 42, 400, 0.04) : 0;
  const purposeW = purposeShown ? textWidth(def.purpose, 34, 400) : 0;
  const half = Math.max(64, labelW / 2, serviceW / 2, purposeW / 2);
  return {
    x1: x - half,
    y1: y - 64,
    x2: x + half,
    y2:
      y - 64 +
      128 +
      16 +
      labelSize +
      (serviceShown && def.service ? 12 + 42 : 0) +
      (purposeShown && def.purpose ? 10 + 34 : 0),
  };
};

const nodeLabelBox = (
  def: NodeDef,
  x: number,
  y: number,
  wide: boolean,
  serviceShown: boolean,
  purposeShown: boolean,
): Box => {
  const full = nodeWorldBox(def, x, y, wide, serviceShown, purposeShown);
  return {x1: full.x1, y1: y + 80, x2: full.x2, y2: full.y2};
};

const screenBox = (box: Box, cam: Camera): Box => ({
  x1: 960 + (box.x1 - cam.cx) * cam.scale,
  y1: 540 + (box.y1 - cam.cy) * cam.scale,
  x2: 960 + (box.x2 - cam.cx) * cam.scale,
  y2: 540 + (box.y2 - cam.cy) * cam.scale,
});

const crossesSafeArea = (box: Box) =>
  box.x1 < SAFE || box.x2 > 1920 - SAFE || box.y1 < SAFE || box.y2 > 1080 - SAFE;

const overlaps = (a: Box, b: Box) =>
  a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;

const insideBox = (box: Box, point: Pt) =>
  point.x >= box.x1 && point.x <= box.x2 && point.y >= box.y1 && point.y <= box.y2;

const glide = (f: number, from: number, to: number) =>
  interpolate(f, [from, to], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const THUMB_W = 104;
const THUMB_H = (THUMB_W * 16) / 9;
const THUMB_GAP = 24;

const thumbParkTop = (pos: Pt): Pt => ({
  x: pos.x - THUMB_W / 2,
  y: pos.y - 64 - THUMB_GAP - THUMB_H,
});

const thumbBoxFrom = (left: number, top: number): Box => ({
  x1: left,
  y1: top,
  x2: left + THUMB_W,
  y2: top + THUMB_H,
});

const ROW_Y = 0;
const ROW_GAP = 360;

const MAIN_ROW: NodeId[] = ['browser', 'website', 'queue', 'check', 'gpu', 'dead'];
const ALL_NODE_IDS: NodeId[] = [...MAIN_ROW, 'storage', 'database', 'watchdog'];

const FIXED_POSITIONS: Record<NodeId, Pt> = (() => {
  const positions = {} as Record<NodeId, Pt>;
  MAIN_ROW.forEach((id, index) => {
    positions[id] = {x: (index - (MAIN_ROW.length - 1) / 2) * ROW_GAP, y: ROW_Y};
  });
  positions.storage = {x: (positions.browser.x + positions.website.x) / 2, y: -340};
  positions.database = {x: positions.website.x, y: 340};
  positions.watchdog = {x: positions.gpu.x, y: -340};
  return positions;
})();

const FIXED_PRESENCE = Object.fromEntries(ALL_NODE_IDS.map((id) => [id, 1])) as Record<NodeId, number>;

const radiusBox = (x: number, y: number, rx: number, ry: number): Box => ({
  x1: x - rx,
  y1: y - ry,
  x2: x + rx,
  y2: y + ry,
});

const thumbTravelExtra = (from: Pt, to: Pt): Box => {
  const a = thumbParkTop(from);
  const b = thumbParkTop(to);
  const control = {x: (a.x + b.x) / 2, y: Math.min(a.y, b.y) - 150};
  const points = Array.from({length: 65}, (_, index) => bez(a, control, b, index / 64));
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  return {
    x1: Math.min(...xs),
    y1: Math.min(...ys),
    x2: Math.max(...xs) + THUMB_W,
    y2: Math.max(...ys) + THUMB_H,
  };
};

type Layout = {
  at: number;
  ids?: NodeId[];
  move?: number;
  extra?: Box[];
  thumbNodes?: NodeId[];
  maxScale?: number;
  height?: number;
};

const linkControl = (path: PathSpec): Pt => {
  if (path.control) return path.control;
  const dx = path.to.x - path.from.x;
  const dy = path.to.y - path.from.y;
  const len = Math.hypot(dx, dy) || 1;
  const bend = path.bend ?? 0;
  return {x: (path.from.x + path.to.x) / 2 + (-dy / len) * bend, y: (path.from.y + path.to.y) / 2 + (dx / len) * bend};
};

const pathPointsOf = (path: PathSpec, count = 24): Pt[] => {
  const control = linkControl(path);
  return Array.from({length: count + 1}, (_, index) => bez(path.from, control, path.to, index / count));
};

const browserStoragePath = (browser: Pt, storage: Pt): PathSpec => ({
  from: {x: browser.x + 42, y: browser.y - 20},
  to: {x: storage.x - 42, y: storage.y - 40},
});

const PRESIGN_GAP = 24;
const PRESIGN_H = 38;

const presignGeometry = (browser: Pt, storage: Pt) => {
  const path = browserStoragePath(browser, storage);
  const points = pathPointsOf(path, 96);
  const top = points.reduce((best, point) => (point.y < best.y ? point : best), points[0]);
  const xs = points.map((point) => point.x);
  const width = textWidth('presigned URL', 26, 400) + 24;
  const iconTop = storage.y - 64;
  const bottom = Math.min(top.y - PRESIGN_GAP, iconTop - PRESIGN_GAP);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = bottom - PRESIGN_H / 2;
  return {
    x: cx,
    y: cy,
    box: {
      x1: cx - width / 2 - 8,
      y1: cy - PRESIGN_H / 2 - 8,
      x2: cx + width / 2 + 8,
      y2: cy + PRESIGN_H / 2 + 8,
    } as Box,
    top,
  };
};

const PRESIGN = presignGeometry(FIXED_POSITIONS.browser, FIXED_POSITIONS.storage);

const WIDE_BOUNDS = (() => {
  const boxes = ALL_NODE_IDS.map((id) =>
    nodeWorldBox(NODES_BY_ID[id], FIXED_POSITIONS[id].x, FIXED_POSITIONS[id].y, false, true, false),
  );
  return unionBox(...boxes);
})();

const WIDE_PAD = 120;
const WIDE_OUTLINE_BOX: Box = {
  x1: WIDE_BOUNDS.x1 - WIDE_PAD,
  y1: WIDE_BOUNDS.y1 - WIDE_PAD,
  x2: WIDE_BOUNDS.x2 + WIDE_PAD,
  y2: WIDE_BOUNDS.y2 + WIDE_PAD,
};
const TF_LABEL_BOX: Box = {
  x1: WIDE_OUTLINE_BOX.x1 + 40,
  y1: WIDE_OUTLINE_BOX.y1 - 86,
  x2: WIDE_OUTLINE_BOX.x1 + 400,
  y2: WIDE_OUTLINE_BOX.y1 - 18,
};

const QUEUE_RACK_EXTRA = [radiusBox(FIXED_POSITIONS.queue.x, -260, 150, 190)];
const CHECK_BOX = nodeWorldBox(NODES_BY_ID.check, FIXED_POSITIONS.check.x, FIXED_POSITIONS.check.y, false, true, true);
const FFPROBE_EXTRA = [{x1: CHECK_BOX.x1 - 40, y1: CHECK_BOX.y2, x2: CHECK_BOX.x2 + 40, y2: CHECK_BOX.y2 + 70}];
const READOUT_SPACE: Box = {
  x1: CHECK_BOX.x1 - 300,
  y1: CHECK_BOX.y2,
  x2: CHECK_BOX.x2 + 300,
  y2: CHECK_BOX.y2 + 220,
};
const DEAD_BOX = nodeWorldBox(NODES_BY_ID.dead, FIXED_POSITIONS.dead.x, FIXED_POSITIONS.dead.y, false, true, true);
const CREDIT_SPACE: Box = {
  x1: DEAD_BOX.x1 - 300,
  y1: DEAD_BOX.y2,
  x2: DEAD_BOX.x2 + 300,
  y2: DEAD_BOX.y2 + 220,
};
const BAD_FILE_EXTRA = radiusBox(FIXED_POSITIONS.check.x - 190, 0, 300, 90);
const RING_EXTRA = radiusBox(FIXED_POSITIONS.gpu.x, FIXED_POSITIONS.gpu.y, 135, 135);
const PANEL_EXTRA: Box = {
  x1: FIXED_POSITIONS.gpu.x - 390,
  y1: FIXED_POSITIONS.gpu.y - 300,
  x2: FIXED_POSITIONS.gpu.x + 390,
  y2: FIXED_POSITIONS.gpu.y + 300,
};
const DUPLICATE_EXTRA = radiusBox(FIXED_POSITIONS.gpu.x, FIXED_POSITIONS.gpu.y + 390, 330, 150);
const THUMB_TRAVEL_EXTRA = thumbTravelExtra(FIXED_POSITIONS.browser, FIXED_POSITIONS.storage);

const LAYOUTS: Layout[] = [
  {
    at: CUE('3b').start,
    ids: ['browser', 'website', 'storage'],
    thumbNodes: ['browser', 'storage'],
    extra: [PRESIGN.box, THUMB_TRAVEL_EXTRA],
    maxScale: 1.35,
  },
  {at: CUE('3c').start, ids: ['website', 'queue'], extra: QUEUE_RACK_EXTRA, maxScale: 1.35},
  {at: CUE('3d').start, ids: ['website', 'queue'], extra: QUEUE_RACK_EXTRA, maxScale: 1.35},
  {
    at: CUE('4').start,
    ids: ['queue', 'check'],
    thumbNodes: ['check'],
    extra: [...FFPROBE_EXTRA, READOUT_SPACE],
    maxScale: 1.3,
  },
  {at: CUE('4r').start, ids: ['check', 'gpu'], extra: [BAD_FILE_EXTRA], maxScale: 1.45},
  {at: CUE('5a').start, ids: ['queue', 'check', 'gpu'], maxScale: 1.15},
  {at: CUE('5b').start, ids: ['gpu'], extra: [RING_EXTRA], maxScale: 1.55},
  {at: CUE('5c').start, ids: ['website', 'database'], maxScale: 1.55},
  {at: CUE('6').start, ids: ['gpu'], extra: [PANEL_EXTRA], maxScale: 1.5},
  {
    at: CUE('7a').start,
    ids: ['check', 'gpu', 'dead'],
    thumbNodes: ['gpu'],
    extra: [CREDIT_SPACE],
    maxScale: 1.05,
  },
  {at: CUE('7b').start, ids: ['gpu'], extra: [DUPLICATE_EXTRA], maxScale: 1.4},
  {at: CUE('7c').start, ids: ['watchdog', 'gpu'], maxScale: 1.55},
  {
    at: CUE('7d').start,
    ids: ALL_NODE_IDS,
    extra: [WIDE_OUTLINE_BOX, TF_LABEL_BOX],
    move: 50,
    maxScale: 1.4,
    height: 0.78,
  },
];

const focusBoxes = (
  boxes: Box[],
  options: {width?: number; height?: number; maxScale?: number} = {},
  centerX?: number,
): Camera => {
  const area = unionBox(...boxes);
  const cx = centerX ?? (area.x1 + area.x2) / 2;
  const cy = (area.y1 + area.y2) / 2;
  const halfW = Math.max(1, cx - area.x1, area.x2 - cx);
  const halfH = Math.max(1, cy - area.y1, area.y2 - cy);
  const scale = Math.min(
    (1920 * (options.width ?? 0.8)) / (halfW * 2),
    (1080 * (options.height ?? 0.74)) / (halfH * 2),
    options.maxScale ?? 1.55,
  );
  return camera(cx, cy, scale);
};

const cameraForLayout = (layout: Layout): Camera => {
  const ids = layout.ids ?? ALL_NODE_IDS;
  const boxes = ids.map((id) =>
    nodeWorldBox(NODES_BY_ID[id], FIXED_POSITIONS[id].x, FIXED_POSITIONS[id].y, false, true, true),
  );
  if (layout.extra) boxes.push(...layout.extra);
  if (layout.thumbNodes) {
    boxes.push(
      ...layout.thumbNodes.map((id) => {
        const park = thumbParkTop(FIXED_POSITIONS[id]);
        return thumbBoxFrom(park.x, park.y);
      }),
    );
  }
  const xs = ids.map((id) => FIXED_POSITIONS[id].x);
  const rowCenter = (Math.min(...xs) + Math.max(...xs)) / 2;
  return focusBoxes(boxes, {maxScale: layout.maxScale, height: layout.height}, rowCenter);
};

const CAMS = LAYOUTS.map((layout) => ({at: layout.at, cam: cameraForLayout(layout), move: layout.move ?? 30}));

const lerpCamera = (a: Camera, b: Camera, t: number): Camera => ({
  cx: lerp(a.cx, b.cx, t),
  cy: lerp(a.cy, b.cy, t),
  scale: lerp(a.scale, b.scale, t),
});

const cameraAt = (f: number): Camera => {
  let index = 0;
  for (let i = 0; i < CAMS.length; i += 1) {
    if (f >= CAMS[i].at) index = i;
    else break;
  }
  const from = CAMS[index];
  const to = CAMS[index + 1];
  if (!to) return from.cam;
  const span = Math.max(1, to.at - from.at);
  const move = Math.min(to.move, span);
  const start = to.at - move;
  if (f > start) return lerpCamera(from.cam, to.cam, glide(f, start, to.at));
  return from.cam;
};

const layoutAt = (f: number) => {
  let index = 0;
  for (let i = 0; i < LAYOUTS.length; i += 1) {
    if (f >= LAYOUTS[i].at) index = i;
    else break;
  }
  return {positions: FIXED_POSITIONS, presence: FIXED_PRESENCE, current: LAYOUTS[index]};
};

const focusIdsAt = (f: number): NodeId[] => layoutAt(f).current.ids ?? ALL_NODE_IDS;

const isWide = (f: number) => f >= CUE('7d').start - 4;

const worldStyle = (cam: Camera): React.CSSProperties => ({
  transform: `translate(${960 - cam.cx * cam.scale}px, ${540 - cam.cy * cam.scale}px) scale(${cam.scale})`,
  transformOrigin: '0 0',
});

const glowOf = (f: number, keys: StateKey[]) => {
  const value: Record<NodeState, number> = {hidden: 0, idle: 0.12, lit: 1, dim: 0.04, off: 0};
  const s = stateAt(f, keys);
  const from = s.prev ? value[s.prev] : value[s.state];
  const t = s.prev ? glide(f, s.at, s.at + 10) : 1;
  return from + (value[s.state] - from) * t;
};

const autoPath = (a: Pt, b: Pt, gap = 88): PathSpec => {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0
      ? {from: {x: a.x + gap, y: a.y}, to: {x: b.x - gap, y: b.y}}
      : {from: {x: a.x - gap, y: a.y}, to: {x: b.x + gap, y: b.y}};
  }
  return dy >= 0
    ? {from: {x: a.x, y: a.y + gap}, to: {x: b.x, y: b.y - gap}}
    : {from: {x: a.x, y: a.y - gap}, to: {x: b.x, y: b.y + gap}};
};

type LinkDraw = {
  id: string;
  path: PathSpec;
  appear: number;
  draw: number;
  arrow?: boolean;
  colour?: string;
  width?: number;
  hot?: number;
  dashed?: boolean;
  dashedAt?: number;
  gap?: number;
  opacity: number;
};

const linksAt = (f: number): LinkDraw[] => {
  const {positions, presence} = layoutAt(f);
  const visible = (id: NodeId) => presence[id] ?? 0;
  const wideOn = f >= CUE('7d').start;
  const inWindow = (from: number, to: number) => f >= from && f < to;
  const pair = (a: NodeId, b: NodeId, from: number, to: number) =>
    Math.min(visible(a), visible(b)) * (inWindow(from, to) || wideOn ? 1 : 0);
  const pos = (id: NodeId) => positions[id];
  const links: LinkDraw[] = [];

  if (pos('browser') && pos('website')) {
    links.push({
      id: 'browser-website',
      path: autoPath(pos('browser'), pos('website'), 84),
      appear: 8,
      draw: 18,
      width: 1.8,
      colour: 'rgba(150,166,188,.42)',
      opacity: pair('browser', 'website', CUE('3b').start, CUE('3c').start),
    });
  }
  if (pos('browser') && pos('storage')) {
    const from = pos('browser');
    const to = pos('storage');
    links.push({
      id: 'browser-storage',
      path: browserStoragePath(from, to),
      appear: CUE('3b').start + 10,
      draw: 16,
      arrow: true,
      hot: ease(f, CUE('3b').start + 6, CUE('3b').start + 18) * (1 - ease(f, CUE('3c').start - 10, CUE('3c').start + 4)),
      opacity: pair('browser', 'storage', CUE('3b').start, CUE('3c').start),
    });
  }
  if (pos('website') && pos('queue')) {
    links.push({
      id: 'website-queue',
      path: autoPath(pos('website'), pos('queue'), 84),
      appear: CUE('3c').start + 10,
      draw: 16,
      arrow: true,
      dashed: inWindow(CUE('3d').start, CUE('4').start),
      dashedAt: CUE('3d').start + 4,
      opacity: pair('website', 'queue', CUE('3c').start, CUE('4').start),
    });
  }
  if (pos('queue') && pos('check')) {
    links.push({
      id: 'queue-check',
      path: autoPath(pos('queue'), pos('check'), 84),
      appear: CUE('4').start + 10,
      draw: 16,
      arrow: true,
      opacity: pair('queue', 'check', CUE('4').start, CUE('5b').start),
    });
  }
  if (pos('check') && pos('gpu')) {
    links.push({
      id: 'check-gpu',
      path: autoPath(pos('check'), pos('gpu'), 84),
      appear: CUE('5a').start + 6,
      draw: 16,
      arrow: true,
      opacity: pair('check', 'gpu', CUE('5a').start, CUE('5b').start),
    });
  }
  if (pos('website') && pos('database')) {
    links.push({
      id: 'website-database',
      path: autoPath(pos('website'), pos('database'), 84),
      appear: CUE('5c').start + 10,
      draw: 16,
      arrow: true,
      opacity: pair('website', 'database', CUE('5c').start, CUE('6').start),
    });
  }
  if (pos('gpu') && pos('dead')) {
    links.push({
      id: 'gpu-dead',
      path: autoPath(pos('gpu'), pos('dead'), 84),
      appear: CUE('7a').start + 10,
      draw: 16,
      arrow: true,
      opacity: pair('gpu', 'dead', CUE('7a').start, CUE('7b').start),
    });
  }
  if (pos('watchdog') && pos('gpu')) {
    links.push({
      id: 'watchdog-gpu',
      path: autoPath(pos('watchdog'), pos('gpu'), 84),
      appear: CUE('7c').start + 10,
      draw: 16,
      arrow: true,
      hot: ease(f, beat('7c', 0.18), beat('7c', 0.3)) * (1 - ease(f, beat('7c', 0.62), beat('7c', 0.78))),
      opacity: pair('watchdog', 'gpu', CUE('7c').start, CUE('7d').start),
    });
  }

  return links.filter((link) => link.opacity > 0.001);
};

const thumbOpacityAt = (f: number) => {
  const in3 = ease(f, 2, 14) * (1 - ease(f, CUE('3c').start - 40, CUE('3c').start - 28));
  const in4 =
    ease(f, CUE('4').start + 8, CUE('4').start + 20) *
    (1 - ease(f, CUE('4r').start - 40, CUE('4r').start - 28));
  const in7 =
    ease(f, CUE('7a').start + 6, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 40, CUE('7b').start - 28));
  return Math.max(in3, in4, in7);
};

const thumbGreyAt = (f: number) => ease(f, beat('7a', 0.28), beat('7a', 0.42));

const thumbStateAt = (f: number) => {
  const {positions} = layoutAt(f);
  let left = 0;
  let top = 0;
  if (f < CUE('3c').start + 4) {
    const fromNode = positions.browser;
    const toNode = positions.storage;
    if (fromNode && toNode) {
      const from = thumbParkTop(fromNode);
      const to = thumbParkTop(toNode);
      const t = glide(f, CUE('3b').start + 8, beat('3b', 0.55));
      const control = {x: (from.x + to.x) / 2, y: Math.min(from.y, to.y) - 150};
      const point = bez(from, control, to, t);
      left = point.x;
      top = point.y;
    }
  } else if (f >= CUE('4').start && f < CUE('4r').start + 8) {
    const check = positions.check;
    if (check) {
      const park = thumbParkTop(check);
      left = park.x;
      top = park.y;
    }
  } else if (f >= CUE('7a').start) {
    const gpu = positions.gpu;
    if (gpu) {
      const park = thumbParkTop(gpu);
      left = park.x;
      top = park.y;
    }
  }
  return {left, top, box: thumbBoxFrom(left, top), opacity: thumbOpacityAt(f), grey: thumbGreyAt(f)};
};

const readoutOpacity = (f: number) =>
  ease(f, CUE('4').start + 8, CUE('4').start + 20) * (1 - ease(f, CUE('4r').start - 10, CUE('4r').start + 4));

const refundOpacity = (f: number) =>
  ease(f, CUE('4r').start + 14, CUE('4r').start + 28) * (1 - ease(f, CUE('5a').start - 10, CUE('5a').start + 4));

const statusOpacity = (f: number) =>
  ease(f, CUE('5a').start - 4, CUE('5a').start + 8) * (1 - ease(f, CUE('5b').start - 8, CUE('5b').start + 4));

const clockOpacity = (f: number) =>
  ease(f, CUE('5b').start - 2, CUE('5b').start + 10) * (1 - ease(f, CUE('5c').start - 10, CUE('5c').start + 4));

const creditOpacity = (f: number) =>
  ease(f, CUE('7a').start - 2, CUE('7a').start + 18) * (1 - ease(f, CUE('7b').start - 40, CUE('7b').start - 26));

const runningAt = (f: number) => {
  const up = beat('5a', 0.2);
  const down = beat('5a', 0.7);
  if (f < up) return 0;
  if (f < down) return ease(f, up, up + 14);
  return 1 - ease(f, down, down + 26);
};

const CARD_GAP = 28;
const FFPROBE_STACK = 42;

type CardId = 'readout' | 'refunded' | 'status' | 'clock' | 'credit';

const CARD_HEIGHT: Record<CardId, number> = {
  readout: 112,
  refunded: 96,
  status: 104,
  clock: 130,
  credit: 210,
};

const cardSize = (id: CardId): {w: number; h: number} => {
  let content = 0;
  if (id === 'readout') {
    content = textWidth('length 0:44', 48, 400) + 12 + 48 + 30 + textWidth('price', 48, 400) + 12 + 48;
  } else if (id === 'refunded') {
    content = 38 + 14 + textWidth('refunded', 44, 400);
  } else if (id === 'status') {
    content = 14 + 14 + textWidth('0 running', 48, 600);
  } else if (id === 'clock') {
    content = 44 + 16 + textWidth('a few minutes to start', 42, 400);
  } else {
    content = Math.max(
      textWidth('credit balance', 44, 600) + 28 + textWidth('held', 44, 600),
      34 + 12 + textWidth('hold returned', 40, 400),
    );
  }
  return {w: Math.ceil(content) + 64, h: CARD_HEIGHT[id]};
};

const CARD_NODE: Record<CardId, NodeId> = {
  readout: 'check',
  refunded: 'check',
  status: 'gpu',
  clock: 'gpu',
  credit: 'dead',
};

const CARD_ORDER: CardId[] = ['readout', 'refunded', 'status', 'clock', 'credit'];

const ffprobeOpacity = (f: number) =>
  ease(f, beat('4', 0.22), beat('4', 0.34)) * (1 - ease(f, CUE('4r').start - 8, CUE('4r').start + 4));

const presignedOpacity = (f: number) =>
  ease(f, CUE('3b').start + 18, CUE('3b').start + 32) * (1 - ease(f, CUE('3c').start - 14, CUE('3c').start));

const CARD_OPACITY: Record<CardId, (f: number) => number> = {
  readout: readoutOpacity,
  refunded: refundOpacity,
  status: statusOpacity,
  clock: clockOpacity,
  credit: creditOpacity,
};

type CardSpec = {id: CardId; box: Box; opacity: number};

const placeCard = (anchor: Box, w: number, h: number, taken: Box[], belowOnly = false): Box => {
  const cx = (anchor.x1 + anchor.x2) / 2;
  const left = (boxLeft: number) => Math.max(SAFE, Math.min(boxLeft, 1920 - SAFE - w));
  const boxLeft = left(cx - w / 2);
  const below: Box = {x1: boxLeft, y1: anchor.y2 + CARD_GAP, x2: boxLeft + w, y2: anchor.y2 + CARD_GAP + h};
  const aboveTop = anchor.y1 - CARD_GAP - h;
  const above: Box = {x1: boxLeft, y1: aboveTop, x2: boxLeft + w, y2: aboveTop + h};
  const blocked = (box: Box) => taken.some((other) => overlaps(box, other));
  const belowFits = below.y2 <= 1080 - SAFE;
  const aboveFits = aboveTop >= SAFE;
  if (belowFits && !blocked(below)) return below;
  if (belowOnly) return below;
  if (aboveFits && !blocked(above)) return above;
  if (aboveFits) return above;
  return below;
};

const takenBoxesAt = (f: number, cam: Camera): Box[] => {
  const boxes = labelTextBoxesAt(f).map((item) => item.box);
  const thumb = thumbStateAt(f);
  if (thumb.opacity > 0.02) boxes.push(screenBox(thumb.box, cam));
  return boxes;
};

const cardSpecsAt = (f: number): CardSpec[] => {
  const {positions} = layoutAt(f);
  const cam = cameraAt(f);
  const taken = takenBoxesAt(f, cam);
  const specs: CardSpec[] = [];
  for (const id of CARD_ORDER) {
    const opacity = CARD_OPACITY[id](f);
    if (opacity <= 0.001) continue;
    const nodeId = CARD_NODE[id];
    const def = NODES_BY_ID[nodeId];
    const pos = positions[nodeId];
    if (!def || !pos) continue;
    const reservePurpose = id !== 'clock';
    let world = nodeWorldBox(def, pos.x, pos.y, false, true, reservePurpose);
    if (id === 'readout' && ffprobeOpacity(f) > 0.02) {
      world = {...world, y2: world.y2 + FFPROBE_STACK};
    }
    const size = cardSize(id);
    const belowOnly = id === 'readout' || id === 'credit';
    specs.push({id, box: placeCard(screenBox(world, cam), size.w, size.h, taken, belowOnly), opacity});
  }
  return specs;
};

const CardShell: React.FC<{box: Box; opacity: number; children: React.ReactNode}> = ({box, opacity, children}) => {
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: box.x1,
        top: box.y1,
        width: box.x2 - box.x1,
        height: box.y2 - box.y1,
        boxSizing: 'border-box',
        padding: '22px 30px',
        borderRadius: 26,
        background: 'rgba(13,15,18,.97)',
        border: `1.5px solid ${C.rule}`,
        boxShadow: '0 20px 50px rgba(0,0,0,.5)',
        fontFamily,
        color: C.text,
        opacity,
      }}
    >
      {children}
    </div>
  );
};

const ReadoutCard: React.FC<{f: number; box: Box; opacity: number}> = ({f, box, opacity}) => {
  const lengthOn = ease(f, beat('4', 0.26), beat('4', 0.36));
  const priceOn = ease(f, beat('4', 0.56), beat('4', 0.68));
  return (
    <CardShell box={box} opacity={opacity}>
      <div style={{display: 'flex', alignItems: 'center', gap: 30, height: '100%', fontSize: 48, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap'}}>
        <span style={{display: 'flex', alignItems: 'center', gap: 12, opacity: lengthOn}}>
          length 0:44 <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={48} /></span>
        </span>
        <span style={{display: 'flex', alignItems: 'center', gap: 12, opacity: priceOn}}>
          price <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={48} /></span>
        </span>
      </div>
    </CardShell>
  );
};

const RefundCard: React.FC<{box: Box; opacity: number}> = ({box, opacity}) => (
  <CardShell box={box} opacity={opacity}>
    <div style={{display: 'flex', alignItems: 'center', gap: 14, height: '100%', fontSize: 44, color: '#C6CCD4'}}>
      <span style={{color: C.hot, display: 'inline-flex'}}><Icon name="tick" size={38} /></span>
      refunded
    </div>
  </CardShell>
);

const StatusCard: React.FC<{f: number; box: Box; opacity: number}> = ({f, box, opacity}) => {
  const running = runningAt(f);
  const live = running > 0.5;
  return (
    <CardShell box={box} opacity={opacity}>
      <div style={{display: 'flex', alignItems: 'center', gap: 14, height: '100%', fontSize: 48, fontWeight: 600, fontVariantNumeric: 'tabular-nums'}}>
        <span style={{width: 14, height: 14, borderRadius: 7, background: live ? C.hot : '#5C6775'}} />
        <span style={{color: live ? '#FFC08A' : '#8E9AAB'}}>{Math.round(running)} running</span>
      </div>
    </CardShell>
  );
};

const ClockCard: React.FC<{box: Box; opacity: number}> = ({box, opacity}) => (
  <CardShell box={box} opacity={opacity}>
    <div style={{display: 'flex', alignItems: 'center', gap: 16, height: '100%', fontSize: 42, color: '#FFC08A', whiteSpace: 'nowrap'}}>
      <Icon name="clock" size={44} /> a few minutes to start
    </div>
  </CardShell>
);

const CreditCard: React.FC<{f: number; box: Box; opacity: number}> = ({f, box, opacity}) => {
  const returned = ease(f, beat('7a', 0.68), beat('7a', 0.9));
  return (
    <CardShell box={box} opacity={opacity}>
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 44, fontWeight: 600}}>
        <span>credit balance</span>
        <span style={{color: C.hot, opacity: 1 - returned}}>held</span>
      </div>
      <div style={{marginTop: 24, height: 22, borderRadius: 11, background: '#232830', overflow: 'hidden'}}>
        <div style={{width: `${100 - 34 * returned}%`, height: '100%', background: C.hot, opacity: 0.82}} />
      </div>
      <div style={{marginTop: 16, height: 40, display: 'flex', alignItems: 'center', gap: 12, fontSize: 40, color: '#FFC08A', opacity: returned}}>
        <Icon name="tick" size={34} /> hold returned
      </div>
    </CardShell>
  );
};

const MonoCaption: React.FC<{text: string; x: number; y: number; opacity: number; pill?: boolean}> = ({
  text,
  x,
  y,
  opacity,
  pill = false,
}) => {
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        transform: 'translate(-50%, -50%)',
        padding: pill ? '3px 10px' : undefined,
        borderRadius: pill ? 8 : undefined,
        background: pill ? 'rgba(11,12,15,.92)' : undefined,
        fontFamily: MONO,
        fontSize: 26,
        letterSpacing: '.02em',
        color: '#8E98A5',
        opacity,
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </div>
  );
};

const QueueRack: React.FC<{f: number; queue: Pt}> = ({f, queue}) => {
  const opacity =
    ease(f, CUE('3c').start + 4, CUE('3c').start + 16) * (1 - ease(f, CUE('4').start - 10, CUE('4').start + 4));
  if (opacity <= 0.001) return null;
  const slots = [-300, -234, -168];
  const drop = glide(f, CUE('3c').start + 12, beat('3c', 0.38));
  const jobTop = queue.y - 450 + (queue.y - 234 - 36 - (queue.y - 450)) * drop;
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      {slots.map((offset, index) => (
        <div
          key={offset}
          style={{
            position: 'absolute',
            left: queue.x - 110,
            top: queue.y + offset - 28,
            width: 220,
            height: 56,
            borderRadius: 12,
            border: `1.4px solid rgba(140,156,178,${index === 1 ? 0.32 : 0.18})`,
            background: index === 1 ? 'rgba(140,156,178,.055)' : 'transparent',
            boxSizing: 'border-box',
          }}
        />
      ))}
      <div
        style={{
          position: 'absolute',
          left: queue.x - 95,
          top: jobTop,
          width: 190,
          height: 72,
          borderRadius: 14,
          background: '#1C1E22',
          border: '1.5px solid rgba(255,158,84,.6)',
          boxShadow: '0 12px 26px rgba(0,0,0,.55)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          fontFamily,
          fontSize: 34,
          fontWeight: 500,
          color: C.text,
        }}
      >
        <div style={{width: 11, height: 11, borderRadius: 6, background: C.hot}} />
        job
      </div>
    </div>
  );
};

const BadFile: React.FC<{f: number; check: Pt}> = ({f, check}) => {
  const opacity =
    ease(f, beat('4r', 0.12), beat('4r', 0.24)) * (1 - ease(f, CUE('5a').start - 8, CUE('5a').start + 4));
  if (opacity <= 0.001) return null;
  const t = glide(f, beat('4r', 0.16), beat('4r', 0.46));
  const start = check.x - 470;
  const end = check.x - 288;
  const left = start + (end - start) * t;
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top: check.y - 55,
        width: 180,
        height: 110,
        boxSizing: 'border-box',
        borderRadius: 16,
        background: '#2A2E35',
        border: '1.5px solid rgba(150,166,188,.38)',
        boxShadow: '0 14px 30px rgba(0,0,0,.5)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        fontFamily,
        fontSize: 30,
        color: '#A7B0BB',
        opacity,
      }}
    >
      <Icon name="storage" size={38} />
      bad file
    </div>
  );
};

const JobArrival: React.FC<{f: number; check: Pt; gpu: Pt}> = ({f, check, gpu}) => {
  const start = beat('5a', 0.1);
  const end = beat('5a', 0.3);
  const opacity =
    ease(f, start, start + 6) * (1 - ease(f, beat('5a', 0.36), beat('5a', 0.44)));
  if (opacity <= 0.001) return null;
  const t = glide(f, start, end);
  const from = check.x + 124;
  const to = gpu.x - 124;
  const left = from + (to - from) * t - 48;
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top: check.y - 24,
        width: 96,
        height: 48,
        borderRadius: 12,
        background: '#1C1E22',
        border: '1.5px solid rgba(255,158,84,.58)',
        boxShadow: '0 10px 22px rgba(0,0,0,.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        fontFamily,
        fontSize: 24,
        color: C.text,
        opacity,
      }}
    >
      <div style={{width: 8, height: 8, borderRadius: 4, background: C.hot}} />
      job
    </div>
  );
};

const badgeCenter = (pos: Pt): Pt => ({x: pos.x + 45, y: pos.y - 45});
const BADGE_SIZE = 68;
const badgeBoxAt = (pos: Pt): Box => {
  const center = badgeCenter(pos);
  return {
    x1: center.x - BADGE_SIZE / 2,
    y1: center.y - BADGE_SIZE / 2,
    x2: center.x + BADGE_SIZE / 2,
    y2: center.y + BADGE_SIZE / 2,
  };
};

const badge4rOpacity = (f: number) =>
  ease(f, beat('4r', 0.34), beat('4r', 0.46)) * (1 - ease(f, CUE('5a').start - 8, CUE('5a').start + 4));

const badge7aAt = (f: number) => {
  const first = ease(f, beat('7a', 0.16), beat('7a', 0.24)) * (1 - ease(f, beat('7a', 0.32), beat('7a', 0.38)));
  const retry = ease(f, beat('7a', 0.34), beat('7a', 0.42)) * (1 - ease(f, beat('7a', 0.5), beat('7a', 0.56)));
  const second = ease(f, beat('7a', 0.52), beat('7a', 0.6));
  const out = 1 - ease(f, CUE('7b').start - 40, CUE('7b').start - 26);
  const kind: 'cross' | 'retry' = f >= beat('7a', 0.45) && f < beat('7a', 0.58) ? 'retry' : 'cross';
  return {opacity: Math.max(first, retry, second) * out, kind, retry: retry * out};
};

const RingBadge: React.FC<{pos: Pt; kind: 'cross' | 'retry'; opacity: number}> = ({pos, kind, opacity}) => {
  if (opacity <= 0.001) return null;
  const center = badgeCenter(pos);
  return (
    <div
      style={{
        position: 'absolute',
        left: center.x - BADGE_SIZE / 2,
        top: center.y - BADGE_SIZE / 2,
        width: BADGE_SIZE,
        height: BADGE_SIZE,
        borderRadius: BADGE_SIZE / 2,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(11,12,15,.96)',
        border: `2px solid ${kind === 'retry' ? 'rgba(255,158,84,.85)' : 'rgba(224,90,79,.9)'}`,
        color: kind === 'retry' ? C.hot : DANGER,
        boxShadow: kind === 'retry' ? '0 0 22px rgba(255,138,42,.3)' : '0 0 22px rgba(224,90,79,.28)',
        opacity,
        transform: `scale(${0.8 + 0.2 * opacity})`,
      }}
    >
      <Icon name={kind} size={38} />
    </div>
  );
};

const RetryLoop: React.FC<{f: number; gpu: Pt; opacity: number}> = ({f, gpu, opacity}) => {
  if (opacity <= 0.001) return null;
  const radius = 78;
  const at = (degrees: number) => {
    const radians = (degrees * Math.PI) / 180;
    return {x: gpu.x + radius * Math.cos(radians), y: gpu.y + radius * Math.sin(radians)};
  };
  const start = at(15);
  const end = at(-75);
  const endRadians = (-75 * Math.PI) / 180;
  const arrow = (Math.atan2(Math.cos(endRadians), -Math.sin(endRadians)) * 180) / Math.PI;
  const size = 16;
  const a = (arrow * Math.PI) / 180;
  const p1 = {x: end.x - size * Math.cos(a - 0.42), y: end.y - size * Math.sin(a - 0.42)};
  const p2 = {x: end.x - size * Math.cos(a + 0.42), y: end.y - size * Math.sin(a + 0.42)};
  return (
    <svg width={1} height={1} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', opacity}}>
      <path
        d={`M ${start.x} ${start.y} A ${radius} ${radius} 0 1 1 ${end.x} ${end.y}`}
        fill="none"
        stroke={C.hot}
        strokeWidth={5}
        strokeLinecap="round"
      />
      <path
        d={`M ${p1.x} ${p1.y} L ${end.x} ${end.y} L ${p2.x} ${p2.y}`}
        fill="none"
        stroke={C.hot}
        strokeWidth={5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const FailureJob: React.FC<{f: number; gpu: Pt; dead: Pt}> = ({f, gpu, dead}) => {
  const start = beat('7a', 0.6);
  const end = beat('7a', 0.86);
  const opacity = ease(f, start, start + 8) * (1 - ease(f, CUE('7b').start - 8, CUE('7b').start + 2));
  if (opacity <= 0.001) return null;
  const t = glide(f, start, end);
  const left = gpu.x + (dead.x - gpu.x) * t - 88;
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top: gpu.y - 40,
        width: 176,
        height: 80,
        borderRadius: 18,
        background: '#1C1E22',
        border: '1.5px solid rgba(224,90,79,.6)',
        boxShadow: '0 12px 26px rgba(0,0,0,.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        fontFamily,
        fontSize: 34,
        color: C.text,
        opacity,
      }}
    >
      <div style={{width: 11, height: 11, borderRadius: 6, background: DANGER}} />
      job
    </div>
  );
};

const DuplicateJobs: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity =
    ease(f, CUE('7b').start + 4, CUE('7b').start + 16) * (1 - ease(f, CUE('7c').start - 10, CUE('7c').start + 2));
  if (opacity <= 0.001) return null;
  const firstIn = glide(f, beat('7b', 0.12), beat('7b', 0.32));
  const secondIn = glide(f, beat('7b', 0.36), beat('7b', 0.56));
  const charged = ease(f, beat('7b', 0.34), beat('7b', 0.46));
  const already = ease(f, beat('7b', 0.58), beat('7b', 0.7));
  const card = (index: number, drop: number) => {
    const x = gpu.x - 240 + index * 260;
    const y = gpu.y + 292 + (1 - drop) * 130;
    return (
      <div
        key={index}
        style={{
          position: 'absolute',
          left: x,
          top: y,
          width: 220,
          height: 96,
          borderRadius: 18,
          background: index === 1 ? '#181A1E' : '#1C1E22',
          border: `1.5px solid ${index === 1 ? 'rgba(120,132,148,.32)' : 'rgba(255,158,84,.55)'}`,
          boxShadow: '0 12px 28px rgba(0,0,0,.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          fontFamily,
          fontSize: 34,
          color: index === 1 ? '#8E98A5' : C.text,
          opacity: index === 1 ? secondIn : firstIn,
        }}
      >
        <div style={{width: 11, height: 11, borderRadius: 6, background: index === 1 ? '#68727F' : C.hot}} />
        job
      </div>
    );
  };
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      {card(0, firstIn)}
      {card(1, secondIn)}
      <div
        style={{
          position: 'absolute',
          left: gpu.x - 240,
          top: gpu.y + 398,
          width: 220,
          textAlign: 'center',
          fontFamily,
          fontSize: 32,
          color: '#FFC08A',
          opacity: charged,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 10,
        }}
      >
        <Icon name="tick" size={30} /> charged
      </div>
      <div
        style={{
          position: 'absolute',
          left: gpu.x + 20,
          top: gpu.y + 398,
          width: 220,
          textAlign: 'center',
          fontFamily,
          fontSize: 30,
          color: '#7D8794',
          opacity: already,
        }}
      >
        already charged
      </div>
    </div>
  );
};

const GpuPanel: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity =
    ease(f, CUE('6').start, CUE('6').start + 24) * (1 - ease(f, CUE('7a').start - 10, CUE('7a').start + 6));
  const pulseStart = CUE('6', 1).start + 8;
  const pulse = Math.sin(Math.PI * clamp((f - pulseStart) / 36));
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: gpu.x - 390,
        top: gpu.y - 300,
        width: 780,
        height: 600,
        boxSizing: 'border-box',
        padding: '22px 36px 24px',
        borderRadius: 44,
        background: '#0B0D10',
        border: '2px solid rgba(255,138,42,.58)',
        boxShadow: '0 0 60px rgba(255,138,42,.18), 0 30px 70px rgba(0,0,0,.7)',
        fontFamily,
        opacity,
        transform: `scale(${0.96 + 0.04 * opacity})`,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16, color: '#FFC08A'}}>
        <Icon name="model" size={48} />
        <div style={{fontSize: 50, fontWeight: 600, letterSpacing: '-.03em', color: C.text}}>TRIBE (Meta)</div>
      </div>
      <div style={{marginTop: 8, fontSize: 30, color: '#8F99A6', letterSpacing: '.005em'}}>
        learned from 700+ people's brain scans
      </div>
      <div style={{display: 'flex', justifyContent: 'center', gap: 40, marginTop: 26}}>
        {[
          {icon: 'speaker' as const, label: 'sound on'},
          {icon: 'muted' as const, label: 'muted'},
        ].map((copy) => (
          <div key={copy.label} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12}}>
            <div
              style={{
                position: 'relative',
                width: 210,
                height: 373,
                borderRadius: 16,
                overflow: 'hidden',
                border: '1.5px solid rgba(255,255,255,.24)',
                boxSizing: 'border-box',
                background: '#000',
              }}
            >
              <Sequence from={CUE('6').start} layout="none">
                <OffthreadVideo
                  src={staticFile(CLIP_VIDEO_SRC)}
                  muted
                  style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}}
                />
              </Sequence>
              {copy.icon === 'muted' && pulse > 0.01 ? (
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'rgba(0,0,0,.28)',
                    opacity: pulse,
                  }}
                >
                  <span style={{color: C.hot, display: 'inline-flex', transform: `scale(${1 + 0.18 * pulse})`}}>
                    <Icon name="muted" size={96} />
                  </span>
                </div>
              ) : null}
            </div>
            <div style={{display: 'flex', alignItems: 'center', gap: 10, fontSize: 32, fontWeight: 500, color: '#C6CCD4'}}>
              <span style={{color: C.hot, display: 'inline-flex'}}><Icon name={copy.icon} size={32} /></span>
              {copy.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const ProgressRing: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity = ease(f, CUE('5b').start + 2, CUE('5b').start + 14) * (1 - ease(f, CUE('5c').start - 8, CUE('5c').start + 4));
  if (opacity <= 0.001) return null;
  const progress = glide(f, CUE('5b').start + 10, CUE('5c').start - 12);
  const r = 70;
  const circumference = 2 * Math.PI * r;
  return (
    <svg width={1} height={1} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible', opacity}}>
      <circle cx={gpu.x} cy={gpu.y} r={r} fill="none" stroke="rgba(120,138,160,.2)" strokeWidth={4} />
      <circle
        cx={gpu.x}
        cy={gpu.y}
        r={r}
        fill="none"
        stroke={C.hot}
        strokeWidth={4}
        strokeLinecap="round"
        strokeDasharray={`${circumference * progress} ${circumference}`}
        transform={`rotate(-90 ${gpu.x} ${gpu.y})`}
      />
    </svg>
  );
};

const TerraformOutline: React.FC<{f: number}> = ({f}) => {
  const draw = ease(f, CUE('7d').start + 4, CUE('7d').start + 44);
  const label = ease(f, CUE('7d').start + 28, CUE('7d').start + 44);
  if (draw <= 0.001) return null;
  return (
    <>
      <div
        style={{
          position: 'absolute',
          left: WIDE_OUTLINE_BOX.x1,
          top: WIDE_OUTLINE_BOX.y1,
          width: WIDE_OUTLINE_BOX.x2 - WIDE_OUTLINE_BOX.x1,
          height: WIDE_OUTLINE_BOX.y2 - WIDE_OUTLINE_BOX.y1,
          border: '3px solid rgba(160,176,198,.68)',
          borderRadius: 64,
          boxSizing: 'border-box',
          opacity: draw,
          transform: `scale(${0.985 + 0.015 * draw})`,
          transformOrigin: 'center',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: TF_LABEL_BOX.x1,
          top: TF_LABEL_BOX.y1,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '10px 22px',
          borderRadius: 999,
          background: 'rgba(11,13,16,.98)',
          border: '1.5px solid rgba(160,176,198,.45)',
          color: '#D6DCE4',
          fontFamily,
          fontSize: 50,
          fontWeight: 600,
          letterSpacing: '-.02em',
          opacity: label,
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{color: '#9FB0C4', display: 'inline-flex'}}><Icon name="terraform" size={42} /></span>
        Terraform
      </div>
    </>
  );
};

export const STILLS = [
  {row: '3b', frame: beat('3b', 0.52)},
  {row: '3c', frame: beat('3c', 0.55)},
  {row: '3d', frame: beat('3d', 0.5)},
  {row: '4', frame: beat('4', 0.62)},
  {row: '4r', frame: beat('4r', 0.72)},
  {row: '5a', frame: beat('5a', 0.5)},
  {row: '5b', frame: beat('5b', 0.52)},
  {row: '5c', frame: beat('5c', 0.5)},
  {row: '6', frame: beat('6', 0.55)},
  {row: '7a', frame: beat('7a', 0.86)},
  {row: '7b', frame: beat('7b', 0.62)},
  {row: '7c', frame: beat('7c', 0.62)},
  {row: '7d', frame: beat('7d', 0.82)},
];

const labelItemsAt = (f: number, textOnly: boolean) => {
  const {positions, presence} = layoutAt(f);
  const cam = cameraAt(f);
  const focus = new Set(focusIdsAt(f));
  const labels: {label: string; box: Box}[] = [];
  for (const def of NODES) {
    if (!focus.has(def.id)) continue;
    const pos = positions[def.id];
    const show = presence[def.id] ?? 0;
    if (!pos || show <= 0.02) continue;
    const state = stateAt(f, def.keys);
    if (state.state === 'hidden') continue;
    const purpose = state.state === 'lit';
    const full = screenBox(nodeWorldBox(def, pos.x, pos.y, false, true, purpose), cam);
    if (crossesSafeArea(full)) continue;
    const worldBox = textOnly
      ? nodeLabelBox(def, pos.x, pos.y, false, true, purpose)
      : nodeWorldBox(def, pos.x, pos.y, false, true, purpose);
    labels.push({label: def.label, box: screenBox(worldBox, cam)});
  }
  return labels;
};

const visibleLabelsAt = (f: number) => labelItemsAt(f, false);
const labelTextBoxesAt = (f: number) => labelItemsAt(f, true);

const badgeBoxesAt = (f: number) => {
  const {positions, presence} = layoutAt(f);
  const cam = cameraAt(f);
  const boxes: {label: string; box: Box}[] = [];
  const check = positions.check;
  if (check && (presence.check ?? 0) > 0.1 && badge4rOpacity(f) > 0.02) {
    boxes.push({label: 'check badge', box: screenBox(badgeBoxAt(check), cam)});
  }
  const gpu = positions.gpu;
  const seven = badge7aAt(f);
  if (gpu && (presence.gpu ?? 0) > 0.1 && seven.opacity > 0.02) {
    boxes.push({label: 'gpu badge', box: screenBox(badgeBoxAt(gpu), cam)});
  }
  return boxes;
};

const SAFE_SAMPLES = (() => {
  const frames = new Map<number, string>();
  for (const still of STILLS) frames.set(still.frame, still.row);
  for (let i = 1; i < CAMS.length; i += 1) {
    const at = CAMS[i].at;
    const move = CAMS[i].move;
    for (let k = 0; k <= 4; k += 1) {
      const f = Math.round(at - move + (move * k) / 4);
      if (f >= 0 && f < DURATION && !frames.has(f)) frames.set(f, `move ${Math.round((k * 100) / 4)}%`);
    }
  }
  for (let f = 0; f < DURATION; f += 20) if (!frames.has(f)) frames.set(f, `scan ${f}`);
  return [...frames.entries()].sort((a, b) => a[0] - b[0]).map(([frame, row]) => ({frame, row}));
})();

export const runSafeAreaCheck = () => {
  let failed = 0;
  for (const sample of SAFE_SAMPLES) {
    const f = sample.frame;
    const {positions, presence} = layoutAt(f);
    const cam = cameraAt(f);
    const labels = visibleLabelsAt(f);
    const labelText = labelTextBoxesAt(f);
    let bad = 0;
    const fail = (message: string) => {
      bad += 1;
      console.warn(`[v6 safe-area] ${sample.row} frame ${f}: ${message}`);
    };

    for (const item of labels) {
      if (crossesSafeArea(item.box)) {
        fail(`${item.label} box ${Math.round(item.box.x1)},${Math.round(item.box.y1)} - ${Math.round(item.box.x2)},${Math.round(item.box.y2)} crosses the ${SAFE}px safe area`);
      }
    }

    const thumb = thumbStateAt(f);
    const thumbScreen = thumb.opacity > 0.02 ? screenBox(thumb.box, cam) : null;
    if (thumbScreen) {
      const box = thumbScreen;
      if (crossesSafeArea(box)) fail('thumbnail crosses the safe area');
      const worldBox = {x1: thumb.box.x1 - 6, y1: thumb.box.y1 - 6, x2: thumb.box.x2 + 6, y2: thumb.box.y2 + 6};
      for (const link of linksAt(f)) {
        if (link.opacity <= 0.02 || f < link.appear) continue;
        for (const point of pathPointsOf(link.path, 28)) {
          if (insideBox(worldBox, point)) {
            fail(`thumbnail crosses link ${link.id}`);
            break;
          }
        }
      }
    }

    for (const badge of badgeBoxesAt(f)) {
      if (crossesSafeArea(badge.box)) fail(`${badge.label} crosses the safe area`);
      for (const label of labelText) if (overlaps(badge.box, label.box)) fail(`${badge.label} overlaps ${label.label} label`);
    }

    for (const card of cardSpecsAt(f)) {
      if (card.opacity <= 0.02) continue;
      if (crossesSafeArea(card.box)) {
        fail(
          `${card.id} card ${Math.round(card.box.x1)},${Math.round(card.box.y1)} - ${Math.round(card.box.x2)},${Math.round(card.box.y2)} crosses the safe area (cam ${cam.cx.toFixed(1)}, ${cam.cy.toFixed(1)}, ${cam.scale.toFixed(2)})`,
        );
      }
      if (thumbScreen && overlaps(card.box, thumbScreen)) fail(`${card.id} card overlaps the thumbnail`);
      for (const label of labelText) if (overlaps(card.box, label.box)) fail(`${card.id} card overlaps ${label.label} label`);
    }

    if (presignedOpacity(f) > 0.02) {
      const gap = (PRESIGN.top.y - (PRESIGN.y + PRESIGN_H / 2)) * cam.scale;
      if (gap < 16) fail(`presigned URL sits ${gap.toFixed(1)}px above the arc (need 16px)`);
    }

    if (isWide(f)) {
      const outline = screenBox(WIDE_OUTLINE_BOX, cam);
      if (crossesSafeArea(outline)) fail('Terraform outline crosses the safe area');
    }

    if (bad > 0) failed += 1;
  }
  if (failed > 0) console.warn(`[v6 safe-area] ${failed}/${SAFE_SAMPLES.length} samples failed`);
  else console.info(`[v6 safe-area] ${SAFE_SAMPLES.length}/${SAFE_SAMPLES.length} samples OK`);
};

export const runNodeStabilityCheck = () => {
  let failed = 0;
  for (let f = 0; f < DURATION; f += 1) {
    const positions = layoutAt(f).positions;
    for (const id of ALL_NODE_IDS) {
      const here = positions[id];
      const fixed = FIXED_POSITIONS[id];
      if (!here || Math.abs(here.x - fixed.x) > 0.001 || Math.abs(here.y - fixed.y) > 0.001) {
        failed += 1;
        console.warn(`[v6 node-stability] ${id} moved at frame ${f}`);
        break;
      }
    }
  }
  if (failed > 0) console.warn(`[v6 node-stability] ${failed}/${DURATION} frames failed`);
  else console.info(`[v6 node-stability] ${DURATION}/${DURATION} frames OK`);
};

let checkQueued = false;
const queueSafeAreaCheck = () => {
  if (checkQueued || typeof window === 'undefined') return;
  checkQueued = true;
  const run = () => {
    runSafeAreaCheck();
    runNodeStabilityCheck();
  };
  if (document.fonts?.ready) document.fonts.ready.then(run).catch(run);
  else run();
};

export const WalkthroughV6Diagram: React.FC = () => {
  const f = useCurrentFrame();
  queueSafeAreaCheck();
  const {positions, presence} = layoutAt(f);
  const cam = cameraAt(f);
  const links = linksAt(f);
  const thumb = thumbStateAt(f);
  const badge4r = badge4rOpacity(f);
  const badge7a = badge7aAt(f);
  const queue = positions.queue;
  const check = positions.check;
  const gpu = positions.gpu;
  const dead = positions.dead;
  const cards = cardSpecsAt(f);
  const checkBottom = check
    ? nodeWorldBox(NODES_BY_ID.check, check.x, check.y, false, true, true).y2
    : 0;
  const ffprobe = ffprobeOpacity(f);

  return (
    <AbsoluteFill style={{background: '#0B0C0F'}}>
      <AbsoluteFill style={worldStyle(cam)}>
        <svg width={1} height={1} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
          {links.map(({id, ...link}) => (
            <Link key={id} {...link} />
          ))}
        </svg>

        {queue && presence.queue > 0.02 ? <QueueRack f={f} queue={queue} /> : null}

        {thumb.opacity > 0.001 ? (
          <div
            style={{
              position: 'absolute',
              left: thumb.left,
              top: thumb.top,
              width: THUMB_W,
              height: THUMB_H,
              borderRadius: 14,
              overflow: 'hidden',
              border: '1.5px solid rgba(255,255,255,.72)',
              boxSizing: 'border-box',
              opacity: thumb.opacity * (1 - 0.3 * thumb.grey),
              filter: thumb.grey > 0.001 ? `grayscale(${thumb.grey}) brightness(${1 - 0.35 * thumb.grey})` : undefined,
              boxShadow:
                thumb.grey > 0.001
                  ? '0 0 0 1px rgba(150,168,190,.28), 0 18px 40px rgba(0,0,0,.6)'
                  : '0 0 0 1px rgba(255,138,42,.4), 0 18px 40px rgba(0,0,0,.7)',
              pointerEvents: 'none',
            }}
          >
            <Img src={staticFile(CLIP_SRC)} style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}} />
          </div>
        ) : null}

        {NODES.map((def) => {
          const pos = positions[def.id];
          const show = presence[def.id] ?? 0;
          if (!pos || show <= 0.001) return null;
          const state = stateAt(f, def.keys);
          if (state.state === 'hidden') return null;
          const purposeOn = state.state === 'lit' ? clamp((glowOf(f, def.keys) - 0.45) / 0.55) : 0;
          const worldBox = nodeWorldBox(def, pos.x, pos.y, false, true, purposeOn > 0.01);
          const screen = screenBox(worldBox, cam);
          const safe = show * clamp(Math.min(screen.x1 - SAFE, 1920 - SAFE - screen.x2, screen.y1 - SAFE, 1080 - SAFE - screen.y2) / 40);
          return (
            <Node
              key={def.id}
              x={pos.x}
              y={pos.y}
              label={def.label}
              service={def.service}
              purpose={def.purpose}
              icon={def.icon}
              state={state.state}
              stateAt={state.at}
              prev={state.prev}
              appear={def.appear}
              wide={false}
              serviceOpacity={1}
              purposeOpacity={purposeOn}
              safe={safe}
            />
          );
        })}

        {check && ffprobe > 0.001 ? (
          <MonoCaption text="ffprobe" x={check.x} y={checkBottom + 22} opacity={ffprobe} />
        ) : null}
        <MonoCaption text="presigned URL" x={PRESIGN.x} y={PRESIGN.y} opacity={presignedOpacity(f)} pill />
        {check ? <BadFile f={f} check={check} /> : null}
        {check && gpu ? <JobArrival f={f} check={check} gpu={gpu} /> : null}
        {gpu && dead ? <FailureJob f={f} gpu={gpu} dead={dead} /> : null}
        {gpu ? <DuplicateJobs f={f} gpu={gpu} /> : null}
        {check ? <RingBadge pos={check} kind="cross" opacity={badge4r} /> : null}
        {gpu ? <RingBadge pos={gpu} kind={badge7a.kind} opacity={badge7a.opacity} /> : null}
        {gpu ? <RetryLoop f={f} gpu={gpu} opacity={badge7a.retry} /> : null}
        {gpu ? <ProgressRing f={f} gpu={gpu} /> : null}
        {gpu ? <GpuPanel f={f} gpu={gpu} /> : null}

        <TerraformOutline f={f} />
      </AbsoluteFill>

      <Caption text="asynchronous" from={CUE('3d').start + 8} to={CUE('4').start - 10} hot="asynchronous" />
      <Caption text="scale to zero" from={beat('5a', 0.62)} to={CUE('5b').start - 8} hot="zero" />
      <Caption text="serverless" from={CUE('5c').start + 6} to={CUE('6').start - 10} hot="serverless" y={985} />
      <Caption text="idempotent" from={CUE('7b').start + 6} to={CUE('7c').start - 10} hot="idempotent" />
      <Caption text="infrastructure as code" from={CUE('7d').start + 8} to={DURATION - 4} hot="code" y={995} />

      {cards.map((card) => {
        if (card.id === 'readout') return <ReadoutCard key={card.id} f={f} box={card.box} opacity={card.opacity} />;
        if (card.id === 'refunded') return <RefundCard key={card.id} box={card.box} opacity={card.opacity} />;
        if (card.id === 'status') return <StatusCard key={card.id} f={f} box={card.box} opacity={card.opacity} />;
        if (card.id === 'clock') return <ClockCard key={card.id} box={card.box} opacity={card.opacity} />;
        return <CreditCard key={card.id} f={f} box={card.box} opacity={card.opacity} />;
      })}
    </AbsoluteFill>
  );
};
