import React, {useMemo} from 'react';
import {AbsoluteFill, Easing, Img, cancelRender, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {clamp, ease} from '../edit/anim';
import {
  Caption,
  DANGER,
  Icon,
  IconName,
  Link,
  Node,
  NODE_GEOMETRY,
  NodeState,
  PathSpec,
  Pt,
  StateKey,
  stateAt,
} from './Diagram';
import {bez, CLIP_SRC, MONO} from './styles/shared';
import envelope from './sound_envelope.json';
import {LineCue, Sfx, buildShots, speechFrame, wordFrame} from './voice';

const FPS = 30;
const SAFE = 80;

// The credit strip is screen space: the bottom 110px of the frame. Subjects are
// framed inside the area above it (1920 x 970) and never enter the strip.
const STRIP_H = 110;
const STRIP_TOP = 1080 - STRIP_H;
const AREA_CX = 960;
const AREA_CY = STRIP_TOP / 2;
const AREA_H = STRIP_TOP;
const BAR_W = 900;
const BAR_H = 18;
const STRIP_BOX: Box = {x1: 0, y1: STRIP_TOP, x2: 1920, y2: 1080};
// The browser's bad reading holds 0.5 min (0.1 of the 5-min bar); shot 4 re-prices it to 1 min (0.2).
const CREDIT_HOLD_BEFORE = 0.1;
const CREDIT_HOLD_AFTER = 0.2;
// Captions sit above the strip; the box height is measured from the rendered pill.
const CAPTION_Y = 836;
const CAPTION_H = 88;
const CAPTION_GAP = 40;
// How far the lit subject's centre may be from the centre of the area above the strip.
const CENTRE_TOLERANCE = 0.04;

// Camera scale per shot in pass 10 (measured); the framing check refuses to go below these.
const PASS10_SCALE: Record<string, number> = {
  '3b': 1.25, // lowered from 1.3341: Storage must be on screen at the same time as the Browser
  '3c': 1.15, // lowered from 1.35: Storage + Queue with their purpose lines are a taller subject than Website + Queue
  '3d': 1.35,
  '4': 0.988,
  '4r': 1.1463,
  '5a': 1.28, // the TRIBE panel (was row 6)
  '5b': 1.15, // the idle cost and scale-to-zero
  '5c': 1.5, // the cold-start clock and ring
  '7a': 0.95, // the GPU-failure cross, refund and idempotent beats share the shot
  '7b': 0.96, // the watchdog (was row 7c)
  '7d': 0.48, // lowered from 0.5069: the Terraform outline must keep clear of the safe margins
};

const WAVE: number[] = (envelope as {samples: number[]}).samples;
const CLIP_FRAMES = ['f0', 'f5', 'f15', 'f25', 'f35'].map(
  (name) => `walkthrough/swee/frames/${name}.jpg`,
);

// The wordless result-return tail after 7b's last word: three legs plus a short rest, derived below.
const RETURN_HOLD = 3.8;

// Each shot is one script line (ids from walkthrough_script.md). The real voice sets every length.
const SHOT_LINES: {row: string; id: string; sectionEnd?: boolean; hold?: number}[] = [
  {row: '3b', id: '3.1'},
  {row: '3c', id: '3.2', hold: 1.2}, // short line: let the job land and the shot settle before the pan
  {row: '3d', id: '3.3', sectionEnd: true},
  {row: '4', id: '4.1'},
  {row: '4r', id: '4.2', sectionEnd: true},
  {row: '5a', id: '5.1'},
  {row: '5b', id: '5.2'},
  {row: '5c', id: '5.3', sectionEnd: true},
  {row: '7a', id: '7.1', hold: 1.8}, // keep "already charged · skipped" on screen long enough to read
  {row: '7b', id: '7.2', sectionEnd: true, hold: RETURN_HOLD}, // tail for the wordless result-return beat
  {row: '7d', id: '7b.1', sectionEnd: true},
];

const built = buildShots(SHOT_LINES.map(({row, id, sectionEnd, hold}) => ({shot: row, ids: [id], sectionEnd, hold})));
export const TIMING = built.cues.map((cue) => ({row: cue.shot, line: cue.lines[0], start: cue.start, end: cue.end}));
export const DURATION = built.duration;
export const LINE_CUES: LineCue[] = TIMING.map((cue) => cue.line);

const CUE = (row: string) => {
  const cue = TIMING.find((line) => line.row === row);
  if (!cue) throw new Error(`no shot "${row}"`);
  return cue;
};

// A fraction of the way through the shot's speech (the hold after the last word is not counted).
const beat = (row: string, progress = 0) => speechFrame(CUE(row).line, progress);

// The frame a word of the shot's line starts (or ends), estimated from the line's length.
const word = (row: string, token: string, occurrence = 0, edge: 'start' | 'end' = 'start') =>
  wordFrame(CUE(row).line, token, occurrence, edge);

// The result-return beat lives in the hold after 7b's last word. The 7d camera pulls back on
// "running up a bill" and is at rest 12 frames into the hold; the beat starts 2 frames after it
// settles, so the whole beat is shown on the 7d wide camera at rest. Three legs: the GPU writes to
// Storage and Database together, both then feed the Website, and the Website returns the result to
// the Browser. The Browser lights as that last pulse nears and "results ready" appears beside it.
// The rest of the hold is a short beat of quiet before 7d's line starts.
const RETURN_HOLD_FRAMES = Math.round(RETURN_HOLD * FPS);
const RETURN_CAMERA_REST = 12;
const RETURN_SETTLE = RETURN_HOLD_FRAMES - RETURN_CAMERA_REST;
const RETURN_START = CUE('7d').start - RETURN_SETTLE + 2;
const RETURN_END = CUE('7b').end - 1;
const RETURN_HOP = 30;
const RETURN_BACK_HOP = 26;
const RETURN_STORAGE_END = RETURN_START + RETURN_HOP;
const RETURN_DATABASE_END = RETURN_STORAGE_END;
const RETURN_BACK_START = RETURN_STORAGE_END;
const RETURN_BACK_END = RETURN_BACK_START + RETURN_BACK_HOP;
const RETURN_BROWSER_START = RETURN_BACK_END;
const RETURN_BROWSER_LIT = RETURN_BROWSER_START + 22;
const RETURN_BROWSER_ARRIVE = RETURN_BROWSER_START + RETURN_HOP;
const RETURN_LABEL_FADE = 10;
const RETURN_LABEL_OUT = RETURN_END - 8;

// Shot 3b: the browser asks the Website for a presigned URL, gets it back, then uploads the clip
// straight to S3. Each leg is given its own slow window so a first-time viewer can follow it.
const ASK_START = CUE('3b').start + 10;
const ASK_END = CUE('3b').start + 40;
const URL_START = CUE('3b').start + 44;
const URL_END = CUE('3b').start + 74;
const UPLOAD_START = CUE('3b').start + 78;
const UPLOAD_ARRIVE = CUE('3b').start + 112;
const WEBSITE_OFF = word('3b', 'website', 0, 'end') + 15;

type NodeId = 'browser' | 'website' | 'storage' | 'queue' | 'check' | 'gpu' | 'watchdog' | 'database';

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
  {at: RETURN_BROWSER_LIT, state: 'lit'},
  {at: RETURN_END + 8, state: 'dim'},
];

const WEBSITE: StateKey[] = [
  {at: CUE('3b').start - 10, state: 'dim'},
  {at: CUE('3b').start, state: 'lit'}, // the browser asks for a presigned URL
  {at: WEBSITE_OFF, state: 'dim'}, // the upload itself never passes through the website
  {at: CUE('3c').start, state: 'idle'}, // context only: S3, not the website, queues the job
  {at: CUE('3d').start, state: 'dim'},
  {at: RETURN_BACK_END, state: 'lit'},
  {at: RETURN_END + 8, state: 'dim'},
];

const STORAGE: StateKey[] = [
  {at: 0, state: 'idle'},
  {at: UPLOAD_ARRIVE, state: 'lit'}, // the clip lands in S3
  {at: CUE('3d').start, state: 'idle'},
  {at: CUE('3d').start + 51, state: 'lit'}, // the second upload passes through S3 on its way to the queue
  {at: CUE('3d').start + 84, state: 'idle'},
  {at: RETURN_STORAGE_END, state: 'lit'},
  {at: RETURN_BACK_END, state: 'idle'},
];

const DATABASE: StateKey[] = [
  {at: 0, state: 'dim'},
  {at: RETURN_DATABASE_END, state: 'lit'},
  {at: RETURN_BACK_END, state: 'dim'},
];

const QUEUE: StateKey[] = [
  {at: CUE('3c').start, state: 'idle'},
  {at: word('3c', 'queue'), state: 'lit'},
  {at: CUE('3d').start, state: 'idle'},
  {at: CUE('3d').start + 105, state: 'lit'},
  {at: CUE('4').start, state: 'lit'},
  {at: beat('4', 0.3), state: 'idle'},
  {at: CUE('5a').start, state: 'lit'},
  {at: word('5a', 'wakes'), state: 'idle'},
];

const CHECK: StateKey[] = [
  {at: CUE('4').start, state: 'lit'},
  {at: CUE('5a').start - 30, state: 'idle'}, // done once it hands the job on; quiet before the camera moves (lit, its purpose line won't fit the frame)
];

const GPU: StateKey[] = [
  {at: CUE('4r').start, state: 'off'},
  {at: word('5a', 'wakes'), state: 'lit'}, // "wakes a GPU on SageMaker"
  {at: word('5b', 'zero'), state: 'off'}, // "scales to zero when idle"
  {at: CUE('5c').start, state: 'idle'}, // starting one takes a few minutes
  {at: CUE('7a').start, state: 'lit'},
  {at: word('7a', 'fails'), state: 'off'},
  {at: word('7a', 'charged'), state: 'lit'}, // "only charged once the result is saved": a normal run that succeeds (the failure has ended with the refund)
  {at: CUE('7b').start, state: 'lit'}, // a stuck job's GPU, before the watchdog
  {at: word('7b', 'running'), state: 'off'},
  {at: CUE('7d').start, state: 'idle'},
];

const WATCHDOG: StateKey[] = [
  {at: CUE('7b').start, state: 'idle'},
  {at: word('7b', 'watchdog'), state: 'lit'},
  {at: beat('7b', 0.72), state: 'idle'},
];

const NODES: NodeDef[] = [
  {id: 'browser', label: 'Browser', service: '', purpose: 'your video', icon: 'browser', keys: BROWSER, appear: 0},
  {id: 'website', label: 'Website', service: 'Lambda', purpose: 'sign-in & upload', icon: 'website', keys: WEBSITE, appear: 0},
  {id: 'storage', label: 'Storage', service: 'S3', purpose: 'videos & results', icon: 'storage', keys: STORAGE, appear: 0},
  {id: 'queue', label: 'Queue', service: 'SQS', purpose: 'jobs wait here', icon: 'queue', keys: QUEUE, appear: CUE('3c').start},
  {id: 'check', label: 'Check', service: 'Lambda', purpose: 'checks length & credit', icon: 'check', keys: CHECK, appear: CUE('4').start},
  {id: 'gpu', label: 'GPU', service: 'SageMaker', purpose: 'runs the brain model', icon: 'gpu', keys: GPU, appear: CUE('4r').start},
  {id: 'watchdog', label: 'Watchdog', service: 'Lambda', purpose: 'stops stuck jobs', icon: 'watchdog', keys: WATCHDOG, appear: CUE('7b').start},
  {id: 'database', label: 'Database', service: 'Aurora', purpose: 'credits, job status', icon: 'database', keys: DATABASE, appear: CUE('7b').start},
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
  const g = NODE_GEOMETRY;
  const labelW = textWidth(def.label, g.label, 600);
  const serviceW = serviceShown ? textWidth(def.service, g.service, 400, 0.04) : 0;
  const purposeW = purposeShown ? textWidth(def.purpose, g.purpose, 400) : 0;
  const half = Math.max(g.iconHalf, labelW / 2, serviceW / 2, purposeW / 2);
  return {
    x1: x - half,
    y1: y - g.iconHalf,
    x2: x + half,
    y2:
      y -
      g.iconHalf +
      g.iconBox +
      g.labelGap +
      g.label +
      (serviceShown && def.service ? g.serviceGap + g.service : 0) +
      (purposeShown && def.purpose ? g.purposeGap + g.purpose : 0),
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
  return {x1: full.x1, y1: y - NODE_GEOMETRY.iconHalf + NODE_GEOMETRY.iconBox + NODE_GEOMETRY.labelGap, x2: full.x2, y2: full.y2};
};

const screenBox = (box: Box, cam: Camera): Box => ({
  x1: AREA_CX + (box.x1 - cam.cx) * cam.scale,
  y1: AREA_CY + (box.y1 - cam.cy) * cam.scale,
  x2: AREA_CX + (box.x2 - cam.cx) * cam.scale,
  y2: AREA_CY + (box.y2 - cam.cy) * cam.scale,
});

const crossesSafeArea = (box: Box) =>
  box.x1 < SAFE || box.x2 > 1920 - SAFE || box.y1 < SAFE || box.y2 > STRIP_TOP;

const overlaps = (a: Box, b: Box, clear = 0) =>
  a.x1 < b.x2 + clear && a.x2 > b.x1 - clear && a.y1 < b.y2 + clear && a.y2 > b.y1 - clear;

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
  y: pos.y - NODE_GEOMETRY.iconHalf - THUMB_GAP - THUMB_H,
});

const thumbBoxFrom = (left: number, top: number): Box => ({
  x1: left,
  y1: top,
  x2: left + THUMB_W,
  y2: top + THUMB_H,
});

const ROW_Y = 0;
// Pass 22: the row is wider so every pair of full labels keeps >= 40px of clearance with the
// pass-19 type sizes. The off-row nodes sit a little closer to the row so the watchdog shot keeps
// its pass-10 scale.
const ROW_GAP = 490;
const OFF_ROW = 480;
const STORAGE_ROW = 348;

const MAIN_ROW: NodeId[] = ['browser', 'website', 'queue', 'check', 'gpu'];
const ALL_NODE_IDS: NodeId[] = [...MAIN_ROW, 'storage', 'watchdog', 'database'];

const FIXED_POSITIONS: Record<NodeId, Pt> = (() => {
  const positions = {} as Record<NodeId, Pt>;
  MAIN_ROW.forEach((id, index) => {
    positions[id] = {x: (index - (MAIN_ROW.length - 1) / 2) * ROW_GAP, y: ROW_Y};
  });
  positions.storage = {x: (positions.browser.x + positions.website.x) / 2, y: -STORAGE_ROW};
  // Watchdog mirrors the Database either side of GPU, so the wide view stays balanced.
  positions.watchdog = {x: positions.gpu.x, y: -OFF_ROW};
  positions.database = {x: positions.gpu.x, y: OFF_ROW};
  return positions;
})();

const FIXED_PRESENCE = Object.fromEntries(ALL_NODE_IDS.map((id) => [id, 1])) as Record<NodeId, number>;

const radiusBox = (x: number, y: number, rx: number, ry: number): Box => ({
  x1: x - rx,
  y1: y - ry,
  x2: x + rx,
  y2: y + ry,
});

type Layout = {
  row: string;
  at: number;
  // Nodes that are lit at the hold: they, `extra` and `thumbNodes` are the subject the camera centres.
  ids?: NodeId[];
  // Nodes the voice line names but that may be dim: they must stay visible, they need not be centred.
  named?: NodeId[];
  // A caption is showing at the hold, at the bottom of the frame, and counts as part of the centred subject.
  caption?: 'below';
  move?: number;
  extra?: Box[];
  thumbNodes?: NodeId[];
  maxScale?: number;
  width?: number;
  height?: number;
  // Frames before this shot's start at which the camera reaches its view (the 7d wide shot settles
  // early so the result-return beat is never shown mid-pull).
  settle?: number;
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

const pathExtra = (path: PathSpec): Box => {
  const points = pathPointsOf(path, 64);
  return {
    x1: Math.min(...points.map((point) => point.x)),
    y1: Math.min(...points.map((point) => point.y)),
    x2: Math.max(...points.map((point) => point.x)),
    y2: Math.max(...points.map((point) => point.y)),
  };
};

const browserStoragePath = (browser: Pt, storage: Pt): PathSpec => ({
  // Leaves and enters each icon sideways on the open left, so the arc never crosses a label.
  from: {x: browser.x - 60, y: browser.y},
  to: {x: storage.x - 60, y: storage.y},
  control: {x: storage.x - 520, y: (browser.y + storage.y) / 2},
});

const storageQueuePath = (storage: Pt, queue: Pt): PathSpec => ({
  // S3 hands the job to SQS: leaves Storage on the open right and enters the queue from the left.
  from: {x: storage.x + 60, y: storage.y},
  to: {x: queue.x - 60, y: queue.y},
});

const nodeServiceBottom = () =>
  -NODE_GEOMETRY.iconHalf +
  NODE_GEOMETRY.iconBox +
  NODE_GEOMETRY.labelGap +
  NODE_GEOMETRY.label +
  NODE_GEOMETRY.serviceGap +
  NODE_GEOMETRY.service;

const rowOffRowPath = (row: Pt, off: Pt): PathSpec => {
  const above = off.y < row.y;
  const gap = 60;
  const end = NODE_GEOMETRY.iconHalf + 16;
  return above
    ? {from: {x: off.x, y: off.y + nodeServiceBottom() + gap}, to: {x: row.x, y: row.y - end}}
    : {from: {x: row.x, y: row.y + nodeServiceBottom() + gap}, to: {x: off.x, y: off.y - end}};
};

// The result-return read paths enter the Website from open space: Storage (above the row) curves
// across from its right side and drops onto the Website's top; Database (below the GPU) runs left at
// its own height and rises into the Website from below its purpose line, never crossing a label.
const storageWebsitePath = (storage: Pt, website: Pt): PathSpec => ({
  from: {x: storage.x + NODE_GEOMETRY.iconHalf + 16, y: storage.y},
  to: {x: website.x, y: website.y - NODE_GEOMETRY.iconHalf - 16},
  control: {x: website.x, y: storage.y},
});

const databaseWebsitePath = (database: Pt, website: Pt): PathSpec => ({
  from: {x: database.x - NODE_GEOMETRY.iconHalf - 16, y: database.y},
  to: {x: website.x, y: website.y + nodeServiceBottom() + NODE_GEOMETRY.purposeGap + NODE_GEOMETRY.purpose + 24},
  control: {x: website.x, y: database.y},
});

const PRESIGN_H = 38;
const PRESIGN_T = 0.5;
// The caption sits just outside the upload arc's apex, on the open left.
const PRESIGN_DX = -130;
const PRESIGN_DY = 0;

const presignGeometry = (browser: Pt, storage: Pt) => {
  const path = browserStoragePath(browser, storage);
  const onArc = bez(path.from, linkControl(path), path.to, PRESIGN_T);
  const point = {x: onArc.x + PRESIGN_DX, y: onArc.y + PRESIGN_DY};
  const width = textWidth('presigned URL', 26, 400) + 24;
  return {
    x: point.x,
    y: point.y,
    box: {
      x1: point.x - width / 2 - 8,
      y1: point.y - PRESIGN_H / 2 - 8,
      x2: point.x + width / 2 + 8,
      y2: point.y + PRESIGN_H / 2 + 8,
    } as Box,
  };
};

const PRESIGN = presignGeometry(FIXED_POSITIONS.browser, FIXED_POSITIONS.storage);
const UPLOAD_ARC_EXTRA = pathExtra(browserStoragePath(FIXED_POSITIONS.browser, FIXED_POSITIONS.storage));
// The Website is lit during the handshake but its purpose line is taller than the row. Frame its
// label box so the shot keeps pass 10's scale; the purpose line still clears the credit strip.
const WEBSITE_FRAME_BOX = nodeWorldBox(
  NODES_BY_ID.website,
  FIXED_POSITIONS.website.x,
  FIXED_POSITIONS.website.y,
  false,
  true,
  false,
);

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

const QUEUE_JOB_W = 190;
const QUEUE_JOB_H = 72;
const QUEUE_JOB_GAP = 12;
const queueJobTop = (queue: Pt, slot = 0) =>
  queue.y - NODE_GEOMETRY.iconHalf - QUEUE_JOB_GAP - QUEUE_JOB_H - slot * (QUEUE_JOB_H + 10);
// The first job, as it sits above the queue once it has dropped in.
const QUEUE_JOB_BOX: Box = {
  x1: FIXED_POSITIONS.queue.x - QUEUE_JOB_W / 2,
  y1: queueJobTop(FIXED_POSITIONS.queue, 0),
  x2: FIXED_POSITIONS.queue.x + QUEUE_JOB_W / 2,
  y2: queueJobTop(FIXED_POSITIONS.queue, 0) + QUEUE_JOB_H,
};
// The two stacked jobs above the queue, as they sit at the end of 3d.
const QUEUE_JOBS_BOX: Box = {
  x1: FIXED_POSITIONS.queue.x - QUEUE_JOB_W / 2,
  y1: queueJobTop(FIXED_POSITIONS.queue, 1),
  x2: FIXED_POSITIONS.queue.x + QUEUE_JOB_W / 2,
  y2: queueJobTop(FIXED_POSITIONS.queue, 0) + QUEUE_JOB_H,
};
const CHECK_BOX = nodeWorldBox(NODES_BY_ID.check, FIXED_POSITIONS.check.x, FIXED_POSITIONS.check.y, false, true, true);
const FFPROBE_EXTRA = [{x1: CHECK_BOX.x1 - 40, y1: CHECK_BOX.y2, x2: CHECK_BOX.x2 + 40, y2: CHECK_BOX.y2 + 70}];
const READOUT_SPACE: Box = {
  x1: CHECK_BOX.x1 - 430,
  y1: CHECK_BOX.y2,
  x2: CHECK_BOX.x2 + 430,
  y2: CHECK_BOX.y2 + 174,
};
// The bad-file card rests just left of Check at the 4r hold; framing centres on where it is then.
const BAD_FILE_EXTRA: Box = {
  x1: FIXED_POSITIONS.check.x - 288,
  y1: FIXED_POSITIONS.check.y - 55,
  x2: FIXED_POSITIONS.check.x - 288 + 180,
  y2: FIXED_POSITIONS.check.y + 55,
};
// The 'waking up' card under the GPU while it boots.
const CLOCK_EXTRA: Box = {
  x1: FIXED_POSITIONS.gpu.x - 178,
  y1: FIXED_POSITIONS.gpu.y + 230,
  x2: FIXED_POSITIONS.gpu.x + 178,
  y2: FIXED_POSITIONS.gpu.y + 330,
};
const RING_EXTRA = radiusBox(FIXED_POSITIONS.gpu.x, FIXED_POSITIONS.gpu.y, 135, 135);
const PANEL_EXTRA: Box = {
  x1: FIXED_POSITIONS.gpu.x - 500,
  y1: FIXED_POSITIONS.gpu.y - 300,
  x2: FIXED_POSITIONS.gpu.x + 500,
  y2: FIXED_POSITIONS.gpu.y + 300,
};
const STATUS_EXTRA: Box = {
  x1: FIXED_POSITIONS.gpu.x - 230,
  y1: FIXED_POSITIONS.gpu.y + 290,
  x2: FIXED_POSITIONS.gpu.x + 230,
  y2: FIXED_POSITIONS.gpu.y + 490,
};
const DUPLICATE_EXTRA: Box = {
  x1: FIXED_POSITIONS.gpu.x - 330,
  y1: FIXED_POSITIONS.gpu.y + 270,
  x2: FIXED_POSITIONS.gpu.x + 330,
  y2: FIXED_POSITIONS.gpu.y + 475,
};

// The frame each shot is judged at: its subject is fully lit and its camera is at rest.
export const STILLS = [
  {row: '3b', frame: beat('3b', 0.52)},
  {row: '3c', frame: word('3c', 'queue', 0, 'end') + 12},
  {row: '3d', frame: CUE('3d').start + 172},
  {row: '4', frame: word('4', 'again') + 8},
  {row: '4r', frame: beat('4r', 0.72)},
  {row: '5a', frame: beat('5a', 0.55)},
  {row: '5b', frame: word('5b', 'scales') + 10},
  {row: '5c', frame: beat('5c', 0.95)}, // the hold, after the 'cold start' caption has gone
  {row: '7a', frame: word('7a', 'idempotent') + 10},
  {row: '7b', frame: beat('7b', 0.42)},
  {row: '7d', frame: beat('7d', 0.82)},
];

const LAYOUTS: Layout[] = [
  {
    row: '3b',
    at: CUE('3b').start,
    ids: ['browser', 'storage'],
    named: ['website'],
    extra: [PRESIGN.box, UPLOAD_ARC_EXTRA, WEBSITE_FRAME_BOX],
    maxScale: 1.26,
    width: 0.84,
  },
  {
    row: '3c',
    at: CUE('3c').start,
    ids: ['storage', 'queue'],
    named: ['website'],
    extra: [QUEUE_JOB_BOX],
    maxScale: 1.35,
  },
  {
    row: '3d',
    at: CUE('3d').start,
    ids: ['queue'],
    named: ['website'],
    extra: [QUEUE_JOBS_BOX],
    maxScale: 1.6,
    width: 0.88,
    caption: 'below',
  },
  {
    row: '4',
    at: CUE('4').start,
    ids: ['check'],
    thumbNodes: ['check'],
    extra: [...FFPROBE_EXTRA, READOUT_SPACE],
    maxScale: 1.3,
    caption: 'below',
  },
  {
    row: '4r',
    at: CUE('4r').start,
    ids: ['check'],
    named: ['gpu'],
    extra: [BAD_FILE_EXTRA],
    maxScale: 1.45,
    width: 0.84,
    move: 18,
  },
  {row: '5a', at: CUE('5a').start, ids: ['gpu'], extra: [PANEL_EXTRA], maxScale: 1.5},
  {row: '5b', at: CUE('5b').start, ids: ['gpu'], extra: [STATUS_EXTRA], maxScale: 1.55},
  {row: '5c', at: CUE('5c').start, ids: ['gpu'], extra: [CLOCK_EXTRA, RING_EXTRA], maxScale: 1.55, caption: 'below'},
  {
    row: '7a',
    at: CUE('7a').start,
    ids: ['gpu'],
    thumbNodes: ['gpu'],
    extra: [DUPLICATE_EXTRA],
    maxScale: 1.5,
    caption: 'below',
  },
  {row: '7b', at: CUE('7b').start, ids: ['watchdog', 'gpu'], maxScale: 1.55},
  {
    row: '7d',
    at: CUE('7d').start,
    ids: ALL_NODE_IDS,
    extra: [WIDE_OUTLINE_BOX, TF_LABEL_BOX],
    move: 50,
    settle: RETURN_SETTLE,
    maxScale: 1.4,
  },
];

const holdOf = (layout: Layout): number => {
  const still = STILLS.find((item) => item.row.split(' ')[0] === layout.row);
  if (!still) throw new Error(`no still (hold frame) for shot ${layout.row}`);
  return still.frame;
};

// A node's box as it is at the hold: its purpose line only takes space while it is lit.
const nodeBoxAt = (id: NodeId, f: number, purpose = true): Box =>
  nodeWorldBox(
    NODES_BY_ID[id],
    FIXED_POSITIONS[id].x,
    FIXED_POSITIONS[id].y,
    false,
    true,
    purpose && stateAt(f, NODES_BY_ID[id].keys).state === 'lit',
  );

// The subject is what is lit at the hold: lit nodes, their cards, thumbnail, panel and arcs.
// Dim context nodes are left out, so they may fall anywhere, including off-frame.
const subjectBoxesForLayout = (layout: Layout): Box[] => {
  const f = holdOf(layout);
  const boxes = (layout.ids ?? ALL_NODE_IDS).map((id) => nodeBoxAt(id, f));
  if (layout.extra) boxes.push(...layout.extra);
  if (layout.thumbNodes) {
    boxes.push(
      ...layout.thumbNodes.map((id) => {
        const park = thumbParkTop(FIXED_POSITIONS[id]);
        return thumbBoxFrom(park.x, park.y);
      }),
    );
  }
  return boxes;
};

const cameraForLayout = (layout: Layout): Camera => {
  const subject = unionBox(...subjectBoxesForLayout(layout));
  const cx = (subject.x1 + subject.x2) / 2;
  const cy = (subject.y1 + subject.y2) / 2;
  // Named nodes must stay on screen around the lit subject even when they are dim. They are framed
  // by their label box: a purpose line taller than the row must not shrink the shot below pass 10.
  const fit = unionBox(subject, ...(layout.named ?? []).map((id) => nodeBoxAt(id, holdOf(layout), false)));
  const halfW = Math.max(1, cx - fit.x1, fit.x2 - cx);
  const halfH = Math.max(1, cy - fit.y1, fit.y2 - cy);
  let scale = Math.min(
    (1920 * (layout.width ?? 0.8)) / (halfW * 2),
    (AREA_H - 2 * SAFE) / (halfH * 2),
    layout.maxScale ?? 1.55,
  );
  if (layout.caption !== 'below') return camera(cx, cy, scale);
  // A caption at the bottom is part of the lit subject: keep the subject clear of it, then put
  // the subject's top as high as the safe area allows (the caption's bottom fixes the other end).
  scale = Math.min(scale, (CAPTION_Y - CAPTION_GAP - SAFE) / (subject.y2 - subject.y1));
  const top = Math.max(SAFE, 2 * AREA_CY - (CAPTION_Y + CAPTION_H));
  return camera(cx, subject.y1 - (top - AREA_CY) / scale, scale);
};

const CAMS = LAYOUTS.map((layout) => ({
  at: layout.at,
  cam: cameraForLayout(layout),
  move: layout.move ?? 30,
  settle: layout.settle ?? 0,
}));

const lerpCamera = (a: Camera, b: Camera, t: number): Camera => ({
  cx: lerp(a.cx, b.cx, t),
  cy: lerp(a.cy, b.cy, t),
  scale: lerp(a.scale, b.scale, t),
});

// The settled camera a frame leaves (from), the one it heads to (to), and how far along the move is (t).
const cameraSpanAt = (f: number): {from: Camera; to: Camera; t: number} => {
  let index = 0;
  for (let i = 0; i < CAMS.length; i += 1) {
    if (f >= CAMS[i].at) index = i;
    else break;
  }
  const from = CAMS[index];
  const to = CAMS[index + 1];
  if (!to) return {from: from.cam, to: from.cam, t: 0};
  const end = to.at - to.settle;
  const span = Math.max(1, end - from.at);
  const move = Math.min(to.move, span);
  const start = end - move;
  return {from: from.cam, to: to.cam, t: f > start ? glide(f, start, end) : 0};
};

const cameraAt = (f: number): Camera => {
  const {from, to, t} = cameraSpanAt(f);
  return t > 0 ? lerpCamera(from, to, t) : from;
};

const layoutAt = (f: number) => {
  let index = 0;
  for (let i = 0; i < LAYOUTS.length; i += 1) {
    if (f >= LAYOUTS[i].at) index = i;
    else break;
  }
  return {positions: FIXED_POSITIONS, presence: FIXED_PRESENCE, current: LAYOUTS[index]};
};

const focusIdsAt = (f: number): NodeId[] => {
  const {ids, named} = layoutAt(f).current;
  return [...(ids ?? ALL_NODE_IDS), ...(named ?? [])];
};

const isWide = (f: number) => f >= CUE('7d').start - 4;

const worldStyle = (cam: Camera): React.CSSProperties => ({
  transform: `translate(${AREA_CX - cam.cx * cam.scale}px, ${AREA_CY - cam.cy * cam.scale}px) scale(${cam.scale})`,
  transformOrigin: '0 0',
});

const glowOf = (f: number, keys: StateKey[]) => {
  const value: Record<NodeState, number> = {hidden: 0, idle: 0.12, lit: 1, dim: 0.04, off: 0};
  const s = stateAt(f, keys);
  const from = s.prev ? value[s.prev] : value[s.state];
  const t = s.prev ? glide(f, s.at, s.at + 10) : 1;
  return from + (value[s.state] - from) * t;
};

// How visible a node is: focus nodes (lit or named) only fade if they overflow the safe area,
// dim context nodes fade out as they near its edge.
const nodeOpacityAt = (def: NodeDef, f: number, cam: Camera): number => {
  const pos = FIXED_POSITIONS[def.id];
  const state = stateAt(f, def.keys);
  const purposeOn = state.state === 'lit' ? clamp((glowOf(f, def.keys) - 0.45) / 0.55) : 0;
  const screen = screenBox(nodeWorldBox(def, pos.x, pos.y, false, true, purposeOn > 0.01), cam);
  const margin = Math.min(screen.x1 - SAFE, 1920 - SAFE - screen.x2, screen.y1 - SAFE, STRIP_TOP - screen.y2);
  const show = FIXED_PRESENCE[def.id];
  // A context (non-focus) node is shown only if it sits inside the settled frame of the shot: judged on
  // the cameras the move leaves and reaches, never the moving camera, so it can't flash in mid-pan.
  const settledIn = (c: Camera) => {
    const b = screenBox(nodeWorldBox(def, pos.x, pos.y, false, true, false), c);
    return clamp(Math.min(b.x1 - SAFE, 1920 - SAFE - b.x2, b.y1 - SAFE, STRIP_TOP - b.y2) / 40);
  };
  const span = cameraSpanAt(f);
  const context = lerp(settledIn(span.from), settledIn(span.to), span.t) * (margin >= 0 ? 1 : clamp(1 + margin / 40));
  const base = show * (new Set(focusIdsAt(f)).has(def.id) ? (margin >= 0 ? 1 : clamp(1 + margin / 40)) : context);
  if (base <= 0) return 0;
  // A card or panel that covers part of this node's label either hides the node completely or
  // leaves it completely clear. A half-covered label is never drawn.
  const label = {...screen, y1: screen.y1 + (NODE_GEOMETRY.iconBox + NODE_GEOMETRY.labelGap) * cam.scale};
  return coveredByOverlayAt(f, def.id, label) ? 0 : base;
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
  gap?: number;
  opacity: number;
};

const linksAt = (f: number): LinkDraw[] => {
  const {positions, presence} = layoutAt(f);
  const cam = cameraAt(f);
  // A link fades with the nodes it joins, so no arrow points at or from a node that is off-frame.
  const visible = (id: NodeId) => (presence[id] ?? 0) * nodeOpacityAt(NODES_BY_ID[id], f, cam);
  const wideOn = f >= CUE('7d').start;
  const inWindow = (from: number, to: number) => f >= from && f < to;
  const pair = (a: NodeId, b: NodeId, from: number, to: number) =>
    Math.min(visible(a), visible(b)) * (inWindow(from, to) || wideOn ? 1 : 0);
  // 3b's handshake rides this link, so it is drawn bright for the shot and fades back to its
  // context weight before 3c takes over.
  const handshake = 1 - ease(f, CUE('3c').start - 30, CUE('3c').start - 6);
  const pos = (id: NodeId) => positions[id];
  const links: LinkDraw[] = [];

  if (pos('browser') && pos('website')) {
    links.push({
      id: 'browser-website',
      path: autoPath(pos('browser'), pos('website'), 84),
      appear: CUE('3b').start,
      draw: 10,
      width: 1.8 + 0.4 * handshake,
      colour: `rgba(150,166,188,${(0.42 + 0.3 * handshake).toFixed(2)})`,
      opacity: pair('browser', 'website', CUE('3b').start, CUE('4').start),
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
      hot: ease(f, UPLOAD_START - 2, UPLOAD_START + 12) * (1 - ease(f, UPLOAD_ARRIVE, UPLOAD_ARRIVE + 12)),
      opacity: pair('browser', 'storage', CUE('3b').start, CUE('3c').start),
    });
  }
  if (pos('storage') && pos('queue')) {
    links.push({
      id: 'storage-queue',
      path: storageQueuePath(pos('storage'), pos('queue')),
      appear: word('3c', 'adds'),
      draw: 16,
      arrow: true,
      opacity: pair('storage', 'queue', CUE('3c').start, CUE('4').start),
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
  if (pos('watchdog') && pos('gpu')) {
    links.push({
      id: 'watchdog-gpu',
      path: rowOffRowPath(pos('gpu'), pos('watchdog')),
      appear: CUE('7b').start + 10,
      draw: 16,
      arrow: true,
      hot: ease(f, word('7b', 'stuck'), word('7b', 'stuck') + 12) * (1 - ease(f, word('7b', 'bill'), word('7b', 'bill') + 12)),
      opacity: pair('watchdog', 'gpu', CUE('7b').start, CUE('7d').start),
    });
  }
  // The worker writes the result and the charge to the database. It is drawn with the same dim,
  // arrowed style as the watchdog link and stays in the map from the watchdog shot onward.
  if (pos('gpu') && pos('database')) {
    links.push({
      id: 'gpu-database',
      path: rowOffRowPath(pos('gpu'), pos('database')),
      appear: CUE('7b').start + 10,
      draw: 16,
      arrow: true,
      hot: ease(f, RETURN_START, RETURN_START + 4) * (1 - ease(f, RETURN_DATABASE_END, RETURN_DATABASE_END + 8)),
      opacity: pair('gpu', 'database', CUE('7b').start, CUE('7d').start),
    });
  }
  const returnOn = inWindow(RETURN_START - 8, RETURN_END + 8);
  if (returnOn && pos('gpu') && pos('storage')) {
    links.push({
      id: 'gpu-storage',
      path: autoPath(pos('gpu'), pos('storage'), 84),
      appear: RETURN_START - 8,
      draw: 8,
      arrow: true,
      width: 2.2,
      colour: 'rgba(150,166,188,.42)',
      opacity: Math.min(visible('gpu'), visible('storage')),
    });
  }
  if (returnOn && pos('storage') && pos('website')) {
    links.push({
      id: 'storage-website',
      path: storageWebsitePath(pos('storage'), pos('website')),
      appear: RETURN_BACK_START - 8,
      draw: 8,
      arrow: true,
      width: 2.2,
      colour: 'rgba(150,166,188,.42)',
      opacity: Math.min(visible('storage'), visible('website')),
    });
  }
  if (returnOn && pos('database') && pos('website')) {
    links.push({
      id: 'database-website',
      path: databaseWebsitePath(pos('database'), pos('website')),
      appear: RETURN_BACK_START - 8,
      draw: 8,
      arrow: true,
      width: 2.2,
      colour: 'rgba(150,166,188,.42)',
      opacity: Math.min(visible('database'), visible('website')),
    });
  }
  if (returnOn && pos('website') && pos('browser')) {
    links.push({
      id: 'website-browser',
      path: autoPath(pos('website'), pos('browser'), 84),
      appear: RETURN_START - 8,
      draw: 8,
      arrow: true,
      width: 2.2,
      colour: 'rgba(150,166,188,.42)',
      opacity: Math.min(visible('website'), visible('browser')),
    });
  }

  return links.filter((link) => link.opacity > 0.001);
};

type PulseHop = {id: string; path: PathSpec; start: number; end: number; from: NodeId; to: NodeId};

// One pulse per hop, over the same path and at the same frames as the clip/job that already
// moves. The result-return hops in 7b's tail are the last five.
const pulsesAt = (): PulseHop[] => {
  const positions = FIXED_POSITIONS;
  const pos = (id: NodeId) => positions[id];
  const hops: PulseHop[] = [];
  if (pos('browser') && pos('website')) {
    hops.push({
      id: 'browser-website-request',
      path: autoPath(pos('browser'), pos('website'), 84),
      start: ASK_START,
      end: ASK_END,
      from: 'browser',
      to: 'website',
    });
  }
  if (pos('website') && pos('browser')) {
    hops.push({
      id: 'website-browser-url',
      path: autoPath(pos('website'), pos('browser'), 84),
      start: URL_START,
      end: URL_END,
      from: 'website',
      to: 'browser',
    });
  }
  if (pos('browser') && pos('storage')) {
    hops.push({
      id: 'browser-storage',
      path: browserStoragePath(pos('browser'), pos('storage')),
      start: UPLOAD_START,
      end: UPLOAD_ARRIVE,
      from: 'browser',
      to: 'storage',
    });
  }
  if (pos('browser') && pos('storage')) {
    hops.push({
      id: 'second-browser-storage',
      path: browserStoragePath(pos('browser'), pos('storage')),
      start: CUE('3d').start + 15,
      end: CUE('3d').start + 75,
      from: 'browser',
      to: 'storage',
    });
  }
  if (pos('storage') && pos('queue')) {
    hops.push({
      id: 'storage-queue',
      path: storageQueuePath(pos('storage'), pos('queue')),
      start: word('3c', 'adds'),
      end: word('3c', 'queue', 0, 'end'),
      from: 'storage',
      to: 'queue',
    });
  }
  if (pos('check') && pos('gpu')) {
    hops.push({
      id: 'check-gpu',
      path: autoPath(pos('check'), pos('gpu'), 84),
      start: word('5a', 'job'),
      end: word('5a', 'GPU', 0, 'end'),
      from: 'check',
      to: 'gpu',
    });
  }
  if (pos('gpu') && pos('storage')) {
    hops.push({
      id: 'return-gpu-storage',
      path: autoPath(pos('gpu'), pos('storage'), 84),
      start: RETURN_START,
      end: RETURN_STORAGE_END,
      from: 'gpu',
      to: 'storage',
    });
  }
  if (pos('gpu') && pos('database')) {
    hops.push({
      id: 'return-gpu-database',
      path: rowOffRowPath(pos('gpu'), pos('database')),
      start: RETURN_START,
      end: RETURN_DATABASE_END,
      from: 'gpu',
      to: 'database',
    });
  }
  if (pos('storage') && pos('website')) {
    hops.push({
      id: 'return-storage-website',
      path: storageWebsitePath(pos('storage'), pos('website')),
      start: RETURN_BACK_START,
      end: RETURN_BACK_END,
      from: 'storage',
      to: 'website',
    });
  }
  if (pos('database') && pos('website')) {
    hops.push({
      id: 'return-database-website',
      path: databaseWebsitePath(pos('database'), pos('website')),
      start: RETURN_BACK_START,
      end: RETURN_BACK_END,
      from: 'database',
      to: 'website',
    });
  }
  if (pos('website') && pos('browser')) {
    hops.push({
      id: 'return-website-browser',
      path: autoPath(pos('website'), pos('browser'), 84),
      start: RETURN_BROWSER_START,
      end: RETURN_BROWSER_ARRIVE,
      from: 'website',
      to: 'browser',
    });
  }
  return hops;
};

const PULSES = pulsesAt();

const LinkPulse: React.FC<{
  path: PathSpec;
  start: number;
  end: number;
  from: NodeId;
  to: NodeId;
  opacity?: number;
}> = ({path, start, end, from, to, opacity = 1}) => {
  const f = useCurrentFrame();
  const cam = cameraAt(f);
  // A pulse only makes sense while the whole link it rides is in shot: both nodes must be drawn
  // and fully on screen, otherwise the dot floats with no line under it.
  const endOnScreen = (id: NodeId) => {
    const def = NODES_BY_ID[id];
    const pos = FIXED_POSITIONS[id];
    if (stateAt(f, def.keys).state === 'hidden' || nodeOpacityAt(def, f, cam) < 0.9) return false;
    const box = screenBox(nodeWorldBox(def, pos.x, pos.y, false, true, false), cam);
    return box.x1 >= 0 && box.x2 <= 1920 && box.y1 >= 0 && box.y2 <= STRIP_TOP;
  };
  const opacityNow =
    ease(f, start, start + 4) * (1 - ease(f, end, end + 8)) * opacity * (endOnScreen(from) && endOnScreen(to) ? 1 : 0);
  if (opacityNow <= 0.001) return null;
  const t = glide(f, start, end);
  const points = pathPointsOf(path, 64);
  const at = (value: number): Pt => {
    const scaled = clamp(value) * (points.length - 1);
    const index = Math.min(points.length - 2, Math.floor(scaled));
    const local = scaled - index;
    return {
      x: points[index].x + (points[index + 1].x - points[index].x) * local,
      y: points[index].y + (points[index + 1].y - points[index].y) * local,
    };
  };
  const head = at(t);
  const tail = Array.from({length: 10}, (_, index) => {
    const key = 1 - (index + 1) / 11;
    return {point: at(Math.max(0, t - 0.016 * (index + 1))), key};
  });
  return (
    <g opacity={opacityNow}>
      {tail.map(({point, key}, index) => (
        <circle key={index} cx={point.x} cy={point.y} r={1.8 + 5.4 * key} fill={C.hot} opacity={0.26 * key} />
      ))}
      <circle cx={head.x} cy={head.y} r={17} fill="rgba(255,138,42,.16)" />
      <circle cx={head.x} cy={head.y} r={9} fill="#FFB47C" />
      <circle cx={head.x} cy={head.y} r={3.8} fill="#FFE2C7" />
    </g>
  );
};

const thumbOpacityAt = (f: number) => {
  const in4 =
    ease(f, CUE('4').start + 8, CUE('4').start + 20) *
    (1 - ease(f, CUE('4r').start - 22, CUE('4r').start - 18));
  const in7 =
    ease(f, CUE('7a').start + 6, CUE('7a').start + 18) *
    (1 - ease(f, CUE('7b').start - 40, CUE('7b').start - 28));
  return Math.max(in4, in7);
};

const thumbGreyAt = (f: number) => ease(f, word('7a', 'twice') - 10, word('7a', 'twice') + 4);

const thumbStateAt = (f: number) => {
  const {positions} = layoutAt(f);
  let left = 0;
  let top = 0;
  if (f >= CUE('4').start && f < CUE('4r').start + 8) {
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
  ease(f, CUE('4').start + 8, CUE('4').start + 20) * (1 - ease(f, CUE('4r').start - 22, CUE('4r').start - 12));

const statusOpacity = (f: number) =>
  ease(f, word('5b', 'month') - 2, word('5b', 'month') + 12) * (1 - ease(f, CUE('5c').start - 10, CUE('5c').start + 2));

const clockOpacity = (f: number) =>
  ease(f, CUE('5c').start - 2, CUE('5c').start + 10) * (1 - ease(f, CUE('7a').start - 10, CUE('7a').start + 4));


// The TRIBE panel opens only once the job card has landed on the GPU ("...wakes a GPU").
const PANEL_IN = () => word('5a', 'GPU', 0, 'end') + 16; // the job card has fully faded (end + 14)
const gpuPanelOpacity = (f: number) =>
  ease(f, PANEL_IN(), PANEL_IN() + 24) * (1 - ease(f, CUE('5b').start, CUE('5b').start + 14));

const runningAt = (f: number) => {
  const up = CUE('5b').start;
  const down = word('5b', 'zero');
  if (f < up) return 0;
  if (f < down) return ease(f, up, up + 14);
  return 1 - ease(f, down, down + 26);
};

const CARD_GAP = 28;
const FFPROBE_STACK = 20;

type CardId = 'readout' | 'status' | 'clock';

const CARD_HEIGHT: Record<CardId, number> = {
  readout: 144,
  status: 176,
  clock: 130,
};

const cardSize = (id: CardId): {w: number; h: number} => {
  let content = 0;
  if (id === 'readout') {
    const GAP = 18;
    content = Math.max(
      textWidth('browser said', 42, 400) + GAP + textWidth('0:25', 42, 600) + GAP + textWidth('→', 42, 400) + GAP + textWidth('file says', 42, 400) + 10 + textWidth('0:44', 42, 600),
      textWidth('credit', 42, 400) + GAP + textWidth('0.5 min', 42, 600) + GAP + textWidth('→', 42, 400) + GAP + textWidth('1 min', 42, 600),
    );
  } else if (id === 'status') {
    content = Math.max(
      textWidth('$1,900 / month', 44, 600),
      textWidth('$0 when idle', 44, 600),
      28 + textWidth('0 running', 42, 600),
    );
  } else {
    content = 44 + 16 + textWidth('waking up', 42, 400);
  }
  return {w: Math.ceil(content) + 64, h: CARD_HEIGHT[id]};
};

const CARD_NODE: Record<CardId, NodeId> = {
  readout: 'check',
  status: 'gpu',
  clock: 'gpu',
};

const CARD_ORDER: CardId[] = ['readout', 'status', 'clock'];

const ffprobeOpacity = (f: number) =>
  ease(f, word('4', 'reads'), word('4', 'reads') + 12) * (1 - ease(f, CUE('4r').start - 8, CUE('4r').start + 4));

const presignedOpacity = (f: number) =>
  ease(f, word('3b', 'presigned'), word('3b', 'presigned') + 12) *
  (1 - ease(f, CUE('3c').start - 32, CUE('3c').start - 18));

const CARD_OPACITY: Record<CardId, (f: number) => number> = {
  readout: readoutOpacity,
  status: statusOpacity,
  clock: clockOpacity,
};

const creditReturnedAt = (f: number) => ease(f, word('7a', 'hold'), word('7a', 'back', 0, 'end'));
const creditChargedAt = (f: number) => ease(f, word('7a', 'once'), word('7a', 'once') + 12);
const badRefundAt = (f: number) => ease(f, word('4r', "you're"), word('4r', 'refunded', 0, 'end'));
const badFileVisibleAt = (f: number) =>
  ease(f, beat('4r', 0.18), beat('4r', 0.3)) * (1 - ease(f, CUE('5a').start - 8, CUE('5a').start + 4));
const creditGlowAt = (f: number) =>
  Math.max(
    ease(f, word('4r', "you're") - 4, word('4r', "you're") + 8) * (1 - ease(f, CUE('5a').start - 10, CUE('5a').start + 2)),
    ease(f, word('7a', 'hold') - 4, word('7a', 'hold') + 8) * (1 - ease(f, word('7a', 'back', 0, 'end'), word('7a', 'back', 0, 'end') + 8)),
    ease(f, word('7a', 'once') - 2, word('7a', 'once') + 10) * (1 - ease(f, word('7a', 'once') + 22, word('7a', 'once') + 34)),
  );
// Shot 4 re-prices the hold from the browser's 0.5 min to the file's 1 min.
const creditHoldShare = (f: number) =>
  lerp(CREDIT_HOLD_BEFORE, CREDIT_HOLD_AFTER, ease(f, word('4', 'credit'), word('4', 'again') + 4));

export const creditWidgetOpacity = (f: number) =>
  ease(f, CUE('3b').start + 6, CUE('3b').start + 20) * (1 - ease(f, CUE('7b').start - 14, CUE('7b').start + 2));

type CardSpec = {id: CardId; box: Box; opacity: number};

const placeCard = (anchor: Box, w: number, h: number, taken: Box[], belowOnly = false): Box => {
  const cx = (anchor.x1 + anchor.x2) / 2;
  const left = (boxLeft: number) => Math.max(SAFE, Math.min(boxLeft, 1920 - SAFE - w));
  const boxLeft = left(cx - w / 2);
  const below: Box = {x1: boxLeft, y1: anchor.y2 + CARD_GAP, x2: boxLeft + w, y2: anchor.y2 + CARD_GAP + h};
  const aboveTop = anchor.y1 - CARD_GAP - h;
  const above: Box = {x1: boxLeft, y1: aboveTop, x2: boxLeft + w, y2: aboveTop + h};
  const blocked = (box: Box) => taken.some((other) => overlaps(box, other));
  const belowFits = below.y2 <= STRIP_TOP;
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
    if (id === 'readout') {
      world = {...world, y2: world.y2 + FFPROBE_STACK};
    }
    const size = cardSize(id);
    const belowOnly = id === 'readout';
    specs.push({id, box: placeCard(screenBox(world, cam), size.w, size.h, taken, belowOnly), opacity});
  }
  return specs;
};

type CoverBox = {label: string; box: Box; opacity: number; owner?: NodeId};

// Every opaque surface that can sit over the map (the TRIBE panel, the live cards, the duplicate
// jobs, the bad-file card and the Terraform pill). A node is hidden whenever one of these would
// cut its label, so nothing peeks out from under a card's edge.
const coverBoxesAt = (() => {
  const cache = new Map<number, CoverBox[]>();
  return (f: number): CoverBox[] => {
    const hit = cache.get(f);
    if (hit) return hit;
    const cam = cameraAt(f);
    const covers: CoverBox[] = [];
    const panel = gpuPanelOpacity(f);
    if (panel > 0.02) {
      covers.push({label: 'TRIBE panel', box: screenBox(PANEL_EXTRA, cam), opacity: panel, owner: 'gpu'});
    }
    for (const card of cardSpecsAt(f)) {
      covers.push({label: `${card.id} card`, box: card.box, opacity: card.opacity, owner: CARD_NODE[card.id]});
    }
    const duplicates = duplicateJobsOpacity(f);
    const gpuPos = FIXED_POSITIONS.gpu;
    const checkPos = FIXED_POSITIONS.check;
    if (duplicates > 0.02) {
      covers.push({
        label: 'duplicate jobs',
        box: {
          x1: gpuPos.x - 260,
          y1: gpuPos.y + 280,
          x2: gpuPos.x + 320,
          y2: gpuPos.y + 476,
        },
        opacity: duplicates,
        owner: 'gpu',
      });
    }
    const badFile = badFileVisibleAt(f);
    if (badFile > 0.02) {
      covers.push({
        label: 'bad file card',
        box: {x1: checkPos.x - 470, y1: checkPos.y - 55, x2: checkPos.x - 108, y2: checkPos.y + 55},
        opacity: badFile,
        owner: 'check',
      });
    }
    const terraform = ease(f, word('7d', 'Terraform'), word('7d', 'Terraform') + 16);
    if (terraform > 0.02) {
      covers.push({label: 'Terraform label', box: screenBox(TF_LABEL_BOX, cam), opacity: terraform});
    }
    cache.set(f, covers);
    return covers;
  };
})();

const coveredByOverlayAt = (f: number, id: NodeId, label: Box): boolean => {
  for (const cover of coverBoxesAt(f)) {
    if (cover.opacity <= 0.02 || cover.owner === id) continue;
    if (overlaps(cover.box, label)) return true;
  }
  return false;
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
        fontFamily,
        color: C.text,
        opacity,
      }}
    >
      {children}
    </div>
  );
};

const Struck: React.FC<{children: React.ReactNode; strike: number}> = ({children, strike}) => (
  <span style={{position: 'relative', display: 'inline-block', color: C.text, opacity: 1 - 0.38 * strike}}>
    {children}
    <span style={{
      position: 'absolute', left: 0, top: '52%', height: 4, width: `${strike * 100}%`, borderRadius: 2,
      background: '#8E98A5',
    }} />
  </span>
);

const ReadoutCard: React.FC<{f: number; box: Box; opacity: number}> = ({f, box, opacity}) => {
  // The browser's reading is struck when the voice says "trusting"; the file's real length lands on
  // "real length". The credit is re-priced on "credit again" (the strip grows with it).
  const browserStrike = ease(f, word('4', 'trusting'), word('4', 'trusting') + 10);
  const fileOn = ease(f, word('4', 'real') + 2, word('4', 'real') + 14);
  const creditStrike = ease(f, word('4', 'credit'), word('4', 'credit') + 10);
  const creditNew = ease(f, word('4', 'credit'), word('4', 'again') + 4);
  const arrow: React.CSSProperties = {color: '#8E98A5'};
  return (
    <CardShell box={box} opacity={opacity}>
      <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 12, height: '100%', fontSize: 42, whiteSpace: 'nowrap'}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
          <span style={{color: '#8E98A5'}}>browser said</span>
          <Struck strike={browserStrike}>0:25</Struck>
          <span style={{...arrow, opacity: fileOn}}>→</span>
          <span style={{display: 'flex', alignItems: 'center', gap: 10, opacity: fileOn}}>
            <span style={{color: '#8E98A5'}}>file says</span>
            <span style={{color: C.hot, fontWeight: 600}}>0:44</span>
          </span>
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
          <span style={{color: '#8E98A5'}}>credit</span>
          <Struck strike={creditStrike}>0.5 min</Struck>
          <span style={{...arrow, opacity: creditNew}}>→</span>
          <span style={{color: C.hot, fontWeight: 600, opacity: creditNew}}>1 min</span>
        </div>
      </div>
    </CardShell>
  );
};

const StatusCard: React.FC<{f: number; box: Box; opacity: number}> = ({f, box, opacity}) => {
  const running = runningAt(f);
  const live = running > 0.5;
  // The card lands on "month"; the month cost then counts up to land on "dollars" and drains on "zero".
  const filled = ease(f, word('5b', 'month'), word('5b', 'dollars'));
  const idleAt = word('5b', 'zero');
  const idle = ease(f, idleAt, idleAt + 18);
  const month = filled * (1 - idle);
  const dollars = Math.round(1900 * month);
  return (
    <CardShell box={box} opacity={opacity}>
      <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 8, height: '100%', fontVariantNumeric: 'tabular-nums'}}>
        <div style={{fontSize: 44, fontWeight: 600, color: live ? '#FFC08A' : '#8E9AAB', whiteSpace: 'nowrap'}}>
          ${dollars.toLocaleString('en-US')} {dollars > 0 ? '/ month' : 'when idle'}
        </div>
        <div style={{width: '100%', height: 5, borderRadius: 3, background: 'rgba(120,132,148,.22)', overflow: 'hidden'}}>
          <div style={{width: `${month * 100}%`, height: '100%', borderRadius: 3, background: live ? C.hot : '#5C6775'}} />
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: 14, fontSize: 42, fontWeight: 600}}>
          <span style={{width: 14, height: 14, borderRadius: 7, background: live ? C.hot : '#5C6775'}} />
          <span style={{color: live ? '#FFC08A' : '#8E9AAB'}}>{Math.round(running)} running</span>
        </div>
      </div>
    </CardShell>
  );
};

const ClockCard: React.FC<{box: Box; opacity: number}> = ({box, opacity}) => (
  <CardShell box={box} opacity={opacity}>
    <div style={{display: 'flex', alignItems: 'center', gap: 16, height: '100%', fontSize: 42, color: '#FFC08A', whiteSpace: 'nowrap'}}>
      <Icon name="clock" size={44} /> waking up
    </div>
  </CardShell>
);

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

// A very low-contrast dot grid on the floor. It lives inside the world layer, so it travels and
// scales with the camera; at 5.5% white it stays below the still-check's lit-pixel threshold.
const FloorGrid: React.FC = () => (
  <div
    style={{
      position: 'absolute',
      left: -3200,
      top: -2600,
      width: 6400,
      height: 5200,
      backgroundImage: 'radial-gradient(circle, rgba(255,255,255,.055) 1.6px, transparent 1.9px)',
      backgroundSize: '80px 80px',
      pointerEvents: 'none',
    }}
  />
);

const FloorGlows: React.FC<{f: number; positions: Record<NodeId, Pt>; cam: Camera}> = ({f, positions, cam}) => (
  <>
    {NODES.map((def) => {
      const pos = positions[def.id];
      if (!pos) return null;
      const glow = glowOf(f, def.keys);
      // Only the active (lit) node casts a floor glow. Dim and idle nodes keep the floor clear,
      // so no soft ellipse sits behind their labels.
      // ...and only as much as the node itself is shown, so a hidden node never leaves a stray glow.
      const active = clamp((glow - 0.3) / 0.5) * nodeOpacityAt(def, f, cam);
      if (active <= 0.01) return null;
      return (
        <div
          key={def.id}
          style={{
            position: 'absolute',
            left: pos.x - 230,
            top: pos.y + 10,
            width: 460,
            height: 180,
            borderRadius: '50%',
            background: `radial-gradient(ellipse at center, rgba(255,138,42,${0.1 * active}), transparent 68%)`,
            filter: 'blur(28px)',
            pointerEvents: 'none',
          }}
        />
      );
    })}
  </>
);

const CreditStrip: React.FC<{f: number}> = ({f}) => {
  const opacity = creditWidgetOpacity(f);
  if (opacity <= 0.001) return null;
  const returned = creditReturnedAt(f);
  const charged = creditChargedAt(f);
  const badVisible = badFileVisibleAt(f);
  const badReturned = badRefundAt(f);
  const glow = creditGlowAt(f);
  const holdShare = creditHoldShare(f);
  const holdWidth = holdShare * (1 - charged);
  const solidWidth = 1 - holdShare + holdWidth * returned;
  const hatchWidth = holdWidth * (1 - returned);
  const refunded = badVisible > 0.5 && badReturned > 0.5;
  const status = charged > 0.5 ? 'charged once' : returned > 0.5 ? 'hold returned' : refunded ? 'refunded' : 'held';
  const lit = charged > 0.02 || returned > 0.02 || glow > 0.02;
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: STRIP_TOP,
        height: STRIP_H,
        boxSizing: 'border-box',
        background: 'rgba(11,12,15,.9)',
        borderTop: `1.5px solid ${lit ? 'rgba(255,158,84,.5)' : C.rule}`,
        boxShadow: lit ? '0 -10px 34px rgba(255,138,42,.1)' : 'none',
        opacity: opacity * (0.8 + 0.2 * glow),
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: SAFE,
          top: 0,
          bottom: 0,
          display: 'flex',
          alignItems: 'center',
          fontFamily,
          fontSize: 34,
          fontWeight: 600,
          letterSpacing: '-.01em',
          color: '#E4E9EF',
          whiteSpace: 'nowrap',
        }}
      >
        5 min credit
      </div>
      <div
        style={{
          position: 'absolute',
          left: AREA_CX - BAR_W / 2,
          top: '50%',
          marginTop: -BAR_H / 2,
          width: BAR_W,
          height: BAR_H,
          borderRadius: BAR_H / 2,
          background: '#232830',
          overflow: 'hidden',
        }}
      >
        <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: `${solidWidth * 100}%`, background: C.hot, opacity: 0.82}} />
        {hatchWidth > 0.001 ? (
          <div
            style={{
              position: 'absolute',
              right: 0,
              top: 0,
              bottom: 0,
              width: `${hatchWidth * 100}%`,
              background: 'repeating-linear-gradient(135deg, rgba(255,158,84,.5) 0 5px, rgba(255,158,84,.08) 5px 11px)',
              borderLeft: '2px solid rgba(255,158,84,.6)',
              boxSizing: 'border-box',
            }}
          />
        ) : null}
      </div>
      <div
        style={{
          position: 'absolute',
          right: SAFE,
          top: 0,
          bottom: 0,
          display: 'flex',
          alignItems: 'center',
          fontFamily,
          fontSize: 34,
          fontWeight: 600,
          letterSpacing: '-.01em',
          color: lit ? '#FFC08A' : '#D6DCE4',
          whiteSpace: 'nowrap',
        }}
      >
        {status}
      </div>
    </div>
  );
};

const QueueRack: React.FC<{f: number; queue: Pt}> = ({f, queue}) => {
  const opacity =
    ease(f, word('3c', 'adds') - 12, word('3c', 'adds')) * (1 - ease(f, CUE('4').start - 10, CUE('4').start + 4));
  if (opacity <= 0.001) return null;
  const drop = glide(f, word('3c', 'adds'), word('3c', 'queue', 0, 'end'));
  const jobTop = lerp(queue.y - 330, queueJobTop(queue, 0), drop);
  const secondStart = CUE('3d').start + 135;
  const secondDrop = glide(f, secondStart, secondStart + 24);
  const secondOn = ease(f, secondStart, secondStart + 10);
  const secondTop = lerp(queue.y - 330, queueJobTop(queue, 1), secondDrop);
  const jobStyle: React.CSSProperties = {
    position: 'absolute',
    left: queue.x - QUEUE_JOB_W / 2,
    width: QUEUE_JOB_W,
    height: QUEUE_JOB_H,
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
  };
  return (
    <div style={{position: 'absolute', inset: 0, opacity}}>
      <div style={{...jobStyle, top: jobTop}}>
        <div style={{width: 11, height: 11, borderRadius: 6, background: C.hot}} />
        job
      </div>
      {secondOn > 0.001 ? (
        <div style={{...jobStyle, top: secondTop, opacity: secondOn}}>
          <div style={{width: 11, height: 11, borderRadius: 6, background: C.hot}} />
          job
        </div>
      ) : null}
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

const SecondUpload: React.FC<{f: number; browser: Pt; storage: Pt; queue: Pt}> = ({f, browser, storage, queue}) => {
  const start = CUE('3d').start + 15;
  const through = start + 60;
  const leave = through + 6;
  const arrive = leave + 60;
  const opacity = ease(f, start, start + 10) * (1 - ease(f, arrive - 8, arrive + 8));
  if (opacity <= 0.001) return null;
  const toStorage = browserStoragePath(browser, storage);
  const toQueue = storageQueuePath(storage, queue);
  const point =
    f < through
      ? bez(toStorage.from, linkControl(toStorage), toStorage.to, glide(f, start, through))
      : bez(toQueue.from, linkControl(toQueue), toQueue.to, glide(f, leave, arrive));
  return (
    <div
      style={{
        position: 'absolute',
        left: point.x - 29,
        top: point.y - 17,
        width: 58,
        height: 34,
        borderRadius: 8,
        overflow: 'hidden',
        border: '1.5px solid rgba(255,158,84,.65)',
        boxSizing: 'border-box',
        opacity,
        boxShadow: '0 8px 20px rgba(0,0,0,.55), 0 0 18px rgba(255,138,42,.22)',
      }}
    >
      <Img src={staticFile(CLIP_SRC)} style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}} />
    </div>
  );
};

const UploadChip: React.FC<{f: number; browser: Pt; storage: Pt}> = ({f, browser, storage}) => {
  const start = UPLOAD_START;
  const arrive = UPLOAD_ARRIVE;
  const opacity = ease(f, start, start + 8) * (1 - ease(f, arrive - 6, arrive + 8));
  if (opacity <= 0.001) return null;
  const path = browserStoragePath(browser, storage);
  const point = bez(path.from, linkControl(path), path.to, glide(f, start, arrive));
  return (
    <div
      style={{
        position: 'absolute',
        left: point.x - 37,
        top: point.y - 21,
        width: 74,
        height: 42,
        borderRadius: 9,
        overflow: 'hidden',
        border: '1.5px solid rgba(255,158,84,.7)',
        boxSizing: 'border-box',
        opacity,
        boxShadow: '0 10px 24px rgba(0,0,0,.6), 0 0 20px rgba(255,138,42,.25)',
      }}
    >
      <Img src={staticFile(CLIP_SRC)} style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}} />
    </div>
  );
};

const JobArrival: React.FC<{f: number; check: Pt; gpu: Pt}> = ({f, check, gpu}) => {
  const start = word('5a', 'job'); // "The job then wakes a GPU"
  const end = word('5a', 'GPU', 0, 'end');
  const opacity = ease(f, start, start + 6) * (1 - ease(f, end + 6, end + 14));
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
  const broke = word('7a', 'fails');
  const back = word('7a', 'back', 0, 'end'); // the failure ends with the refund
  return ease(f, broke, broke + 10) * (1 - ease(f, back, back + 12));
};

const RingBadge: React.FC<{pos: Pt; opacity: number}> = ({pos, opacity}) => {
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
        border: '2px solid rgba(224,90,79,.9)',
        color: DANGER,
        boxShadow: '0 0 22px rgba(224,90,79,.28)',
        opacity,
        transform: `scale(${0.8 + 0.2 * opacity})`,
      }}
    >
      <Icon name="cross" size={38} />
    </div>
  );
};

const duplicateJobsOpacity = (f: number) =>
  ease(f, word('7a', 'charged') - 10, word('7a', 'charged') + 4) * (1 - ease(f, CUE('7b').start - 10, CUE('7b').start + 2));

const DuplicateJobs: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity = duplicateJobsOpacity(f);
  if (opacity <= 0.001) return null;
  const firstIn = glide(f, word('7a', 'charged') - 6, word('7a', 'charged') + 18);
  // The duplicate is the same job #42, and arrives on "a job is never charged twice".
  const secondIn = glide(f, word('7a', 'never'), word('7a', 'twice'));
  const charged = ease(f, word('7a', 'once'), word('7a', 'once') + 12);
  const already = ease(f, word('7a', 'twice'), word('7a', 'twice') + 10); // the word lands as it shows
  const card = (index: number, drop: number) => {
    const x = gpu.x - 240 + index * 260;
    const y = gpu.y + 292 + (1 - drop) * 130;
    const dimmed = index === 1 ? already : 0;
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
          background: '#1C1E22',
          border: '1.5px solid rgba(255,158,84,.55)',
          boxShadow: '0 12px 28px rgba(0,0,0,.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          fontFamily,
          fontSize: 34,
          color: C.text,
          opacity: drop * (1 - 0.5 * dimmed),
          filter: dimmed > 0.001 ? `brightness(${1 - 0.3 * dimmed}) saturate(${1 - 0.5 * dimmed})` : undefined,
        }}
      >
        <div style={{width: 11, height: 11, borderRadius: 6, background: C.hot}} />
        job #42
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
          left: gpu.x - 260,
          top: gpu.y + 412,
          width: 260,
          height: 52,
          boxSizing: 'border-box',
          borderRadius: 10,
          border: '2px solid rgba(255,158,84,.75)',
          background: 'rgba(255,138,42,.06)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          fontFamily,
          fontSize: 26,
          fontWeight: 600,
          letterSpacing: '.01em',
          color: '#FFC08A',
          opacity: charged,
          whiteSpace: 'nowrap',
        }}
      >
        <Icon name="tick" size={26} /> charged
      </div>
      <div
        style={{
          position: 'absolute',
          left: gpu.x + 20,
          top: gpu.y + 412,
          boxSizing: 'border-box',
          borderRadius: 10,
          border: '1.5px solid rgba(120,132,148,.32)',
          background: 'rgba(120,132,148,.12)',
          padding: '8px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily,
          fontSize: 22,
          fontWeight: 500,
          letterSpacing: '.01em',
          color: '#9AA3AE',
          whiteSpace: 'nowrap',
          opacity: already,
        }}
      >
        already charged · skipped
      </div>
    </div>
  );
};

const GpuPanel: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity = gpuPanelOpacity(f);
  if (opacity <= 0.001) return null;
  const start = PANEL_IN();
  const passOn = ease(f, start + 4, start + 18);
  const inputBrightness = 0.45 + 0.55 * passOn;
  const captionOn = ease(f, start + 6, start + 18);
  const playhead = glide(f, start + 4, CUE('5b').start);
  const coreGlow = passOn;
  const cellW = 106;
  const phase = ((f - start) / FPS) * 0.55 % 5;
  const cells = Array.from({length: 10}, (_, index) => CLIP_FRAMES[index % CLIP_FRAMES.length]);
  const barHeight = (value: number) => 10 + value * 64;
  return (
    <div
      style={{
        position: 'absolute',
        left: gpu.x - 500,
        top: gpu.y - 300,
        width: 1000,
        height: 600,
        boxSizing: 'border-box',
        borderRadius: 44,
        background: '#0B0D10',
        border: '2px solid rgba(255,138,42,.58)',
        boxShadow: '0 0 60px rgba(255,138,42,.18)',
        fontFamily,
        opacity,
        transform: `scale(${0.96 + 0.04 * opacity})`,
      }}
    >
      <div style={{position: 'absolute', left: 60, top: 22, display: 'flex', alignItems: 'center', gap: 16, color: '#FFC08A'}}>
        <Icon name="model" size={44} />
        <div style={{fontSize: 50, fontWeight: 600, letterSpacing: '-.03em', color: C.text}}>TRIBE v2 (Meta)</div>
      </div>
      <div style={{position: 'absolute', left: 60, top: 80, fontSize: 30, color: '#8F99A6', letterSpacing: '.005em'}}>
        learned from 700+ people's brain scans
      </div>

      <div style={{position: 'absolute', left: 60, top: 134, fontSize: 24, letterSpacing: '.08em', color: '#8E98A5', textTransform: 'uppercase', opacity: inputBrightness}}>
        picture
      </div>
      <div
        style={{
          position: 'absolute',
          left: 60,
          top: 164,
          width: 600,
          height: 188,
          borderRadius: 14,
          overflow: 'hidden',
          border: '1.5px solid rgba(255,255,255,.24)',
          boxSizing: 'border-box',
          background: '#000',
          opacity: inputBrightness,
        }}
      >
        <div style={{position: 'absolute', left: -136 - phase * cellW, top: 0, display: 'flex', height: '100%'}}>
          {cells.concat(cells).map((src, index) => (
            <div key={index} style={{width: cellW, height: '100%', overflow: 'hidden', borderRight: '1px solid rgba(0,0,0,.65)'}}>
              <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}} />
            </div>
          ))}
        </div>
      </div>

      <div style={{position: 'absolute', left: 60, top: 370, fontSize: 24, letterSpacing: '.08em', color: '#8E98A5', textTransform: 'uppercase', opacity: inputBrightness}}>
        sound
      </div>
      <div
        style={{
          position: 'absolute',
          left: 60,
          top: 400,
          width: 600,
          height: 92,
          borderRadius: 12,
          overflow: 'hidden',
          border: '1.5px solid rgba(255,255,255,.2)',
          boxSizing: 'border-box',
          background: '#0E1014',
          opacity: inputBrightness,
        }}
      >
        <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', padding: '0 10px'}}>
          {WAVE.map((value, index) => {
            const played = index / WAVE.length < playhead;
            return (
              <div
                key={index}
                style={{
                  flex: 1,
                  height: barHeight(value),
                  borderRadius: 2,
                  background: played ? 'rgba(255,178,122,.88)' : 'rgba(255,158,84,.42)',
                }}
              />
            );
          })}
        </div>
        <div style={{position: 'absolute', top: 0, bottom: 0, left: `${(10 / 600) * 100}%`, width: 2, background: C.hot, opacity: passOn, transform: `translateX(${playhead * 578}px)`}} />
      </div>

      <div style={{position: 'absolute', left: 664, top: 256, width: 52, height: 2, background: '#FFB47C', opacity: passOn * 0.8}} />
      <div style={{position: 'absolute', left: 712, top: 251, width: 0, height: 0, borderTop: '6px solid transparent', borderBottom: '6px solid transparent', borderLeft: `10px solid #FFB47C`, opacity: passOn * 0.8}} />
      <div style={{position: 'absolute', left: 664, top: 445, width: 52, height: 2, background: '#FFB47C', opacity: passOn * 0.8}} />
      <div style={{position: 'absolute', left: 712, top: 440, width: 0, height: 0, borderTop: '6px solid transparent', borderBottom: '6px solid transparent', borderLeft: `10px solid #FFB47C`, opacity: passOn * 0.8}} />

      <div
        style={{
          position: 'absolute',
          left: 720,
          top: 166,
          width: 230,
          height: 324,
          borderRadius: 30,
          border: `2px solid rgba(255,138,42,${0.36 + 0.4 * coreGlow})`,
          background: 'radial-gradient(circle at 50% 45%, rgba(255,138,42,.16), #0B0D10 70%)',
          boxShadow: `0 0 ${36 + 44 * coreGlow}px rgba(255,138,42,${0.1 + 0.14 * coreGlow})`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <span style={{color: '#FFC08A', display: 'inline-flex', transform: `scale(${1 + 0.04 * coreGlow})`}}>
          <Icon name="model" size={96} />
        </span>
      </div>

      <div style={{position: 'absolute', left: 60, top: 514, display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 30, fontWeight: 500, opacity: captionOn, whiteSpace: 'nowrap'}}>
        <span style={{color: '#FFC08A'}}>picture</span>
        <span style={{color: '#8E98A5'}}>+</span>
        <span style={{color: '#FFC08A'}}>sound</span>
      </div>
    </div>
  );
};

const ProgressRing: React.FC<{f: number; gpu: Pt}> = ({f, gpu}) => {
  const opacity = ease(f, CUE('5c').start + 2, CUE('5c').start + 14) * (1 - ease(f, CUE('7a').start - 8, CUE('7a').start + 4));
  if (opacity <= 0.001) return null;
  const progress = glide(f, CUE('5c').start + 10, CUE('7a').start - 12);
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
  const label = ease(f, word('7d', 'Terraform'), word('7d', 'Terraform') + 16);
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
  if (gpu && (presence.gpu ?? 0) > 0.1 && seven > 0.02) {
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
      const presignBox = screenBox(PRESIGN.box, cam);
      if (crossesSafeArea(presignBox)) fail('presigned URL crosses the safe area');
      for (const label of labelText) {
        if (overlaps(presignBox, label.box)) fail(`presigned URL overlaps ${label.label} label`);
      }
    }

    const arc = linksAt(f).find((link) => link.id === 'browser-storage');
    if (arc && arc.opacity > 0.02) {
      const arcPoints = pathPointsOf(arc.path, 40).map((point) => ({
        x: 960 + (point.x - cam.cx) * cam.scale,
        y: AREA_CY + (point.y - cam.cy) * cam.scale,
      }));
      for (const label of labelText) {
        // The upload arc ends under Storage's own label. Links are drawn below nodes, so that
        // label sits on top of the arc; only a crossing of some other label is a real problem.
        if (label.label === NODES_BY_ID.storage.label) continue;
        const hit = arcPoints.some(
          (point) =>
            point.x >= label.box.x1 - 4 &&
            point.x <= label.box.x2 + 4 &&
            point.y >= label.box.y1 - 4 &&
            point.y <= label.box.y2 + 4,
        );
        if (hit) fail(`upload arc crosses ${label.label} label`);
      }
    }

    const stripVisible = creditWidgetOpacity(f) > 0.02;
    if (stripVisible) {
      for (const label of labelText) if (overlaps(STRIP_BOX, label.box)) fail(`${label.label} label overlaps the credit strip`);
      if (thumbScreen && overlaps(STRIP_BOX, thumbScreen)) fail('thumbnail overlaps the credit strip');
      for (const card of cardSpecsAt(f)) {
        if (card.opacity > 0.02 && overlaps(STRIP_BOX, card.box)) fail(`${card.id} card overlaps the credit strip`);
      }
      if (gpuPanelOpacity(f) > 0.02 && overlaps(STRIP_BOX, screenBox(PANEL_EXTRA, cam), 4)) {
        fail('TRIBE panel overlaps the credit strip');
      }
      if (presignedOpacity(f) > 0.02 && overlaps(STRIP_BOX, screenBox(PRESIGN.box, cam))) {
        fail('presigned URL overlaps the credit strip');
      }
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

// World-space clearance check: nodes never move, so this runs once. Every pair of full label
// boxes (name + service + purpose at their largest size) must keep at least 40px between them.
// The still-frame checks missed the purpose lines that collide only mid-pan; this one cannot.
const LABEL_CLEARANCE = 40;

export const runLabelClearanceCheck = (): string[] => {
  const failures: string[] = [];
  const boxes = ALL_NODE_IDS.map((id) => ({
    id,
    box: nodeLabelBox(NODES_BY_ID[id], FIXED_POSITIONS[id].x, FIXED_POSITIONS[id].y, false, true, true),
  }));
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      if (overlaps(boxes[i].box, boxes[j].box, LABEL_CLEARANCE)) {
        const a = boxes[i].box;
        const b = boxes[j].box;
        failures.push(
          `${NODES_BY_ID[boxes[i].id].label} and ${NODES_BY_ID[boxes[j].id].label} labels are closer than ${LABEL_CLEARANCE}px ` +
            `(A ${Math.round(a.x1)},${Math.round(a.y1)}-${Math.round(a.x2)},${Math.round(a.y2)}; ` +
            `B ${Math.round(b.x1)},${Math.round(b.y1)}-${Math.round(b.x2)},${Math.round(b.y2)})`,
        );
      }
    }
  }
  return failures;
};

// Card/panel overlap check: on every frame a card or panel is showing, every other node's label
// is either completely clear of it or completely hidden (never half under its edge). Runs once.
export const runCardOverlapCheck = (): string[] => {
  const failures: string[] = [];
  for (let f = 0; f < DURATION; f += 1) {
    const cam = cameraAt(f);
    const covers = coverBoxesAt(f);
    if (covers.length === 0) continue;
    for (const cover of covers) {
      if (cover.opacity <= 0.02) continue;
      for (const def of NODES) {
        if (cover.owner === def.id) continue;
        const pos = FIXED_POSITIONS[def.id];
        if (!pos) continue;
        const state = stateAt(f, def.keys);
        if (state.state === 'hidden') continue;
        const label = screenBox(nodeLabelBox(def, pos.x, pos.y, false, true, state.state === 'lit'), cam);
        if (!overlaps(cover.box, label)) continue;
        const fullyInside =
          label.x1 >= cover.box.x1 && label.x2 <= cover.box.x2 && label.y1 >= cover.box.y1 && label.y2 <= cover.box.y2;
        if (fullyInside) continue;
        if (nodeOpacityAt(def, f, cam) > 0.02) {
          failures.push(
            `frame ${f}: ${cover.label} cuts the ${def.label} label ` +
              `(${Math.round(label.x1)},${Math.round(label.y1)}-${Math.round(label.x2)},${Math.round(label.y2)} ` +
              `vs ${Math.round(cover.box.x1)},${Math.round(cover.box.y1)}-${Math.round(cover.box.x2)},${Math.round(cover.box.y2)})`,
          );
          if (failures.length >= 20) return failures;
        }
      }
    }
  }
  return failures;
};

// Framing check: at every still's hold the lit subject (plus the caption, when one is showing)
// must sit centred in the area above the credit strip, clear of the strip, no smaller than in
// pass 10, and every node the voice line names must be visible. The rendered stills are
// measured the same way from their bright pixels by check_stills.mjs.
export const runFramingCheck = (): string[] => {
  const failures: string[] = [];
  for (const still of STILLS) {
    const row = still.row.split(' ')[0];
    const layout = LAYOUTS.find((item) => item.row === row);
    const fail = (message: string) => failures.push(`shot ${still.row} (frame ${still.frame}): ${message}`);
    if (!layout) {
      fail('no layout for this still');
      continue;
    }
    const f = still.frame;
    const cam = cameraAt(f);
    const subject = screenBox(unionBox(...subjectBoxesForLayout(layout)), cam);
    const caption: Box = {x1: subject.x1, y1: CAPTION_Y, x2: subject.x2, y2: CAPTION_Y + CAPTION_H};
    const lit = layout.caption ? unionBox(subject, caption) : subject;
    const cx = (lit.x1 + lit.x2) / 2;
    const cy = (lit.y1 + lit.y2) / 2;
    if (Math.abs(cx - AREA_CX) > CENTRE_TOLERANCE * 1920) {
      fail(`lit subject centre x ${Math.round(cx)} is more than ${CENTRE_TOLERANCE * 100}% off ${AREA_CX}`);
    }
    if (Math.abs(cy - AREA_CY) > CENTRE_TOLERANCE * AREA_H) {
      fail(`lit subject centre y ${Math.round(cy)} is more than ${CENTRE_TOLERANCE * 100}% off ${AREA_CY}`);
    }
    if (lit.y2 > STRIP_TOP) fail(`subject overlaps the credit strip (bottom edge ${Math.round(lit.y2)} > ${STRIP_TOP})`);
    if (layout.caption === 'below' && subject.y2 > CAPTION_Y - CAPTION_GAP / 2) {
      fail(`subject comes within ${CAPTION_GAP / 2}px of the caption (bottom edge ${Math.round(subject.y2)})`);
    }
    if (layout.caption === 'below') {
      for (const card of cardSpecsAt(f)) {
        if (card.opacity > 0.02 && card.box.y2 > CAPTION_Y - CAPTION_GAP / 2) fail(`${card.id} card comes within ${CAPTION_GAP / 2}px of the caption (gap ${Math.round(CAPTION_Y - card.box.y2)})`);
      }
    }
    const pass10 = PASS10_SCALE[layout.row];
    if (pass10 !== undefined && cam.scale < pass10 - 1e-4) {
      fail(`camera scale ${cam.scale.toFixed(4)} is smaller than pass 10's ${pass10}`);
    }
    for (const id of new Set([...(layout.ids ?? ALL_NODE_IDS), ...(layout.named ?? [])])) {
      const def = NODES_BY_ID[id];
      if (stateAt(f, def.keys).state === 'hidden') fail(`${def.label} is hidden`);
      else if (nodeOpacityAt(def, f, cam) < 0.99) fail(`${def.label} is not fully visible inside the safe area`);
    }
  }
  return failures;
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

// Sound effects, each on the frame its visual lands (placed and mixed in Full.tsx).
export const DIAGRAM_SFX: Sfx[] = [
  {frame: word('3c', 'queue', 0, 'end'), sound: 'tick'}, // the first job lands in the queue
  {frame: CUE('3d').start + 135 + 24, sound: 'tick'}, // the second job lands
  {frame: word('4', 'trusting'), sound: 'scribble'}, // 0:25 struck out
  {frame: word('4', 'credit'), sound: 'scribble'}, // 0.5 min struck out
  {frame: beat('4r', 0.34), sound: 'denied'}, // the bad file's cross
  {frame: word('7a', 'once'), sound: 'stamp'}, // the "charged" stamp
  {frame: word('7a', 'twice'), sound: 'skipped'}, // the duplicate job #42: "already charged · skipped"
  {frame: RETURN_BROWSER_LIT, sound: 'chime'}, // the result reaches the Browser
];

export const WalkthroughV6Diagram: React.FC = () => {
  const f = useCurrentFrame();
  const framingFailures = useMemo(runFramingCheck, []);
  if (framingFailures.length > 0) {
    cancelRender(
      new Error(
        `[v6 framing] ${framingFailures.length} framing failure(s) at the shots' holds:\n${framingFailures.join('\n')}`,
      ),
    );
  }
  const clearanceFailures = useMemo(runLabelClearanceCheck, []);
  if (clearanceFailures.length > 0) {
    cancelRender(
      new Error(
        `[v6 label clearance] ${clearanceFailures.length} node label pair(s) closer than ${LABEL_CLEARANCE}px:\n${clearanceFailures.join('\n')}`,
      ),
    );
  }
  const overlapFailures = useMemo(runCardOverlapCheck, []);
  if (overlapFailures.length > 0) {
    cancelRender(
      new Error(
        `[v6 card overlap] ${overlapFailures.length} label(s) half-covered by a card or panel:\n${overlapFailures.join('\n')}`,
      ),
    );
  }
  queueSafeAreaCheck();
  const {positions, presence} = layoutAt(f);
  const cam = cameraAt(f);
  const links = linksAt(f);
  const thumb = thumbStateAt(f);
  const badge4r = badge4rOpacity(f);
  const badge7a = badge7aAt(f);
  const browser = positions.browser;
  const queue = positions.queue;
  const check = positions.check;
  const gpu = positions.gpu;
  const cards = cardSpecsAt(f);
  const checkBottom = check
    ? nodeWorldBox(NODES_BY_ID.check, check.x, check.y, false, true, true).y2
    : 0;
  const ffprobe = ffprobeOpacity(f);
  // The result is ready in the Browser: a small mono note fades in under its purpose line and
  // leaves with the beat. It is measured in screen space so it stays ~24px on the wide camera.
  const resultsReady =
    ease(f, RETURN_BROWSER_LIT, RETURN_BROWSER_LIT + RETURN_LABEL_FADE) * (1 - ease(f, RETURN_LABEL_OUT, RETURN_END));
  const browserBox = browser
    ? screenBox(nodeWorldBox(NODES_BY_ID.browser, browser.x, browser.y, false, true, true), cam)
    : null;

  return (
    <AbsoluteFill style={{background: '#0B0C0F'}}>
      <AbsoluteFill style={worldStyle(cam)}>
        <FloorGrid />
        <FloorGlows f={f} positions={positions} cam={cam} />
        <svg width={1} height={1} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
          {links.map(({id, ...link}) => (
            <Link key={id} {...link} />
          ))}
          {PULSES.map((hop) => (
            <LinkPulse key={hop.id} path={hop.path} start={hop.start} end={hop.end} from={hop.from} to={hop.to} />
          ))}
        </svg>

        {queue && presence.queue > 0.02 ? <QueueRack f={f} queue={queue} /> : null}
        {browser && positions.storage && queue ? (
          <SecondUpload f={f} browser={browser} storage={positions.storage} queue={queue} />
        ) : null}
        {browser && positions.storage ? <UploadChip f={f} browser={browser} storage={positions.storage} /> : null}

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
          // The TRIBE panel sits right over the GPU: the GPU fades out ahead of it and back in after it,
          // so its label never shows through the panel while the panel fades.
          const underPanel = def.id === 'gpu' ? clamp((0.12 - gpuPanelOpacity(f)) / 0.12) : 1;
          const safe = nodeOpacityAt(def, f, cam) * underPanel;
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
        {gpu ? <DuplicateJobs f={f} gpu={gpu} /> : null}
        {check ? <RingBadge pos={check} opacity={badge4r} /> : null}
        {gpu ? <RingBadge pos={gpu} opacity={badge7a} /> : null}
        {gpu ? <ProgressRing f={f} gpu={gpu} /> : null}
        {gpu ? <GpuPanel f={f} gpu={gpu} /> : null}

        <TerraformOutline f={f} />
      </AbsoluteFill>

      {resultsReady > 0.001 && browserBox ? (
        <div
          style={{
            position: 'absolute',
            left: (browserBox.x1 + browserBox.x2) / 2,
            top: browserBox.y2 + 18,
            transform: 'translateX(-50%)',
            fontFamily: MONO,
            fontSize: 24,
            fontWeight: 500,
            letterSpacing: '.06em',
            color: C.hot,
            opacity: resultsReady,
            whiteSpace: 'nowrap',
          }}
        >
          results ready
        </div>
      ) : null}

      <Caption y={CAPTION_Y} text="asynchronous" from={CUE('3d').start + 8} to={CUE('4').start - 10} hot="asynchronous" />
      <Caption y={CAPTION_Y} text="server-side validation" from={word('4', 'server-side') - 8} to={CUE('4r').start} hot="validation" />
      <Caption y={CAPTION_Y} text="scale to zero" from={word('5b', 'scales') - 8} to={CUE('5c').start - 8} hot="zero" />
      <Caption y={CAPTION_Y} text="cold start" from={word('5c', 'cold') - 8} to={word('5c', 'which') - 8} hot="start" />
      <Caption y={CAPTION_Y} text="idempotent" from={word('7a', 'idempotent') - 8} to={word('7a', 'twice') - 6} hot="idempotent" />
      <Caption y={CAPTION_Y} text="infrastructure as code" from={word('7d', 'infrastructure') - 8} to={DURATION - 4} hot="code" />

      {cards.map((card) => {
        if (card.id === 'readout') return <ReadoutCard key={card.id} f={f} box={card.box} opacity={card.opacity} />;
        if (card.id === 'status') return <StatusCard key={card.id} f={f} box={card.box} opacity={card.opacity} />;
        return <ClockCard key={card.id} box={card.box} opacity={card.opacity} />;
      })}

      <CreditStrip f={f} />
    </AbsoluteFill>
  );
};
