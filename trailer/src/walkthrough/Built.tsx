import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {BUILT, FPS, at} from './timeline';

const easeOut = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.out(Easing.cubic),
});
const easeIO = (t: number, a: number, b: number) => interpolate(t, [a, b], [0, 1], {
  extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  easing: Easing.inOut(Easing.cubic),
});

type Box = {
  id: string; title: string; label: string;
  x: number; y: number; w: number; h: number;
  delay: number; dx: number; dy: number; rot?: number;
  glow?: number;
};

const boxes: Box[] = [
  {id: 'browser', title: 'Browser', label: 'your file', x: 50, y: 210, w: 300, h: 170, delay: 0.12, dx: -120, dy: 80, rot: -2},
  {id: 's3', title: 'Storage', label: 'S3', x: 390, y: 210, w: 300, h: 170, delay: 0.24, dx: 50, dy: -110, rot: 1.4},
  {id: 'queue', title: 'Queue', label: 'SQS', x: 730, y: 210, w: 300, h: 170, delay: 0.36, dx: 30, dy: -140, rot: 2},
  {id: 'check', title: 'Check', label: 'Lambda · length + price', x: 1070, y: 210, w: 300, h: 170, delay: 0.48, dx: 110, dy: -80, rot: 1},
  {id: 'gpu', title: 'GPU', label: 'idle: $0 / hour', x: 1410, y: 200, w: 300, h: 190, delay: 0.60, dx: 160, dy: 40, rot: 2.4},
  {id: 'website', title: 'Website', label: 'CloudFront + Lambda', x: 50, y: 440, w: 300, h: 170, delay: 0.72, dx: -90, dy: 130, rot: -1.2},
  {id: 'brain', title: 'Brain', label: 'TRIBE v2', x: 390, y: 440, w: 300, h: 170, delay: 0.84, dx: -40, dy: 140, rot: 1.5},
  {id: 'credits', title: 'Credit', label: 'held / charged', x: 730, y: 440, w: 300, h: 170, delay: 0.96, dx: 100, dy: 130, rot: -1.8},
  {id: 'response', title: 'Response', label: 'second by second', x: 1070, y: 440, w: 300, h: 170, delay: 1.08, dx: 80, dy: 100, rot: 1.2},
  {id: 'report', title: 'Report', label: 'Bedrock · Claude', x: 1410, y: 440, w: 300, h: 170, delay: 1.20, dx: 150, dy: 120, rot: -2},
];

const Card: React.FC<{box: Box; t: number}> = ({box, t}) => {
  const p = easeOut(t, box.delay, box.delay + 0.72);
  const hot = (box.glow ?? 0) > 0.02;
  return <div style={{
    position: 'absolute',
    left: box.x + box.dx * (1 - p),
    top: box.y + box.dy * (1 - p),
    width: box.w, height: box.h,
    opacity: p,
    transform: `rotate(${(box.rot ?? 0) * (1 - p)}deg) scale(${0.92 + p * 0.08})`,
    transformOrigin: '50% 50%',
    background: C.surface, borderRadius: 15, boxSizing: 'border-box',
    border: `1.5px solid ${hot ? C.hot : C.rule}`,
    boxShadow: hot ? `0 0 0 1px ${C.hot}, 0 0 34px rgba(255,138,42,.28)` : '0 14px 34px rgba(0,0,0,.38)',
    display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 22px',
    fontFamily, color: C.text,
  }}>
    <div style={{fontSize: 38, lineHeight: 1.1, fontWeight: 600, letterSpacing: '-0.028em'}}>{box.title}</div>
    <div style={{marginTop: 9, fontSize: 22, lineHeight: 1.25, color: C.muted, letterSpacing: '-0.005em'}}>{box.label}</div>
  </div>;
};

export const Built: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const frameIn = easeOut(t, 0, 0.5);
  const watchAt = at(22) - BUILT.start;
  const watch = easeOut(t, watchAt - 0.2, watchAt + 0.4);
  const tick = easeOut(t, watchAt + 0.5, watchAt + 1.0);
  const gpuDim = easeIO(t, at(22) - BUILT.start + 1.2, at(22) - BUILT.start + 2.2);
  const checkLen = 90;

  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, #0B0C0F 0%, #000 74%)', fontFamily, color: C.text}}>
    <div style={{
      position: 'absolute', left: 80, top: 80, width: 1760, height: 920,
      borderRadius: 22, boxSizing: 'border-box', border: `1.5px solid ${C.rule}`,
      opacity: frameIn, boxShadow: '0 30px 90px rgba(0,0,0,.6)',
    }} />
    <div style={{
      position: 'absolute', left: 130, top: 118, opacity: frameIn,
      fontSize: 42, fontWeight: 600, letterSpacing: '-0.032em',
    }}>Terraform</div>
    <div style={{
      position: 'absolute', left: 130, top: 170, opacity: frameIn,
      fontSize: 24, color: C.muted, letterSpacing: '-0.005em',
    }}>one command up, one command down</div>

    <div style={{position: 'absolute', left: 80, top: 80, width: 1760, height: 920}}>
      <svg width={1760} height={920} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        <defs>
          <marker id="built-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4B515A" />
          </marker>
        </defs>
        <path d="M 350 295 L 380 295" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 690 295 L 720 295" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 1030 295 L 1060 295" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 1370 295 L 1400 295" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 200 380 L 200 432" fill="none" stroke="#4B515A" strokeWidth={2} strokeDasharray="8 9" opacity={frameIn * 0.7} />
        <path d="M 1220 380 L 880 432" fill="none" stroke="#4B515A" strokeWidth={2} strokeDasharray="8 9" opacity={frameIn * 0.7} />
        <path d="M 690 525 L 1060 525" fill="none" stroke="#4B515A" strokeWidth={2} opacity={frameIn * 0.7} />
        <path d="M 1560 390 L 1560 432" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 1030 755 L 1560 755 L 1560 390" fill="none" stroke="#FF8A2A"
          strokeWidth={2.5} strokeDasharray="8 9" opacity={watch * 0.8} />
      </svg>

      {boxes.map((box) => <Card key={box.id} box={box} t={t} />)}

      <div style={{
        position: 'absolute', left: 730, top: 670, width: 300, height: 170,
        opacity: watch, transform: `translateY(${(1 - watch) * 14}px)`,
        background: C.surface, borderRadius: 15, border: `1.5px solid ${C.hot}`,
        boxShadow: '0 14px 34px rgba(0,0,0,.38), 0 0 32px rgba(255,138,42,.2)',
        display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 22px',
        fontFamily, color: C.text, boxSizing: 'border-box',
      }}>
        <div style={{fontSize: 38, lineHeight: 1.1, fontWeight: 600, letterSpacing: '-0.028em'}}>Watchdog</div>
        <div style={{marginTop: 9, fontSize: 22, color: C.muted}}>every 10 min</div>
        <svg width={38} height={32} viewBox="0 0 34 28" style={{
          position: 'absolute', right: 20, bottom: 18, overflow: 'visible',
        }}>
          <polyline points="3,15 12,24 31,4" fill="none" stroke={C.hot} strokeWidth={3.2}
            strokeLinecap="round" strokeLinejoin="round"
            strokeDasharray={checkLen} strokeDashoffset={checkLen * (1 - tick)} opacity={tick > 0 ? 1 : 0} />
        </svg>
      </div>

      <div style={{
        position: 'absolute', left: 1410, top: 200, width: 300, height: 190,
        borderRadius: 15, pointerEvents: 'none',
        background: `rgba(0,0,0,${gpuDim * 0.24})`,
      }} />
      <div style={{
        position: 'absolute', left: 1432, top: 344, fontFamily, fontSize: 22,
        color: C.hot, fontWeight: 500, opacity: gpuDim,
      }}>off · $0 / hour</div>
    </div>
  </AbsoluteFill>;
};
