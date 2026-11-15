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
  {id: 'browser', title: 'Browser', label: 'your file', x: 60, y: 270, w: 220, h: 130, delay: 0.15, dx: -120, dy: 80, rot: -2},
  {id: 's3', title: 'Storage', label: 'S3', x: 330, y: 270, w: 220, h: 130, delay: 0.28, dx: 50, dy: -110, rot: 1.4},
  {id: 'website', title: 'Website', label: 'CloudFront + Lambda', x: 60, y: 456, w: 220, h: 130, delay: 0.42, dx: -90, dy: 130, rot: -1.2},
  {id: 'queue', title: 'Queue', label: 'SQS', x: 600, y: 270, w: 220, h: 130, delay: 0.56, dx: 30, dy: -140, rot: 2},
  {id: 'check', title: 'Check', label: 'Lambda · length + price', x: 880, y: 270, w: 250, h: 130, delay: 0.7, dx: 110, dy: -80, rot: 1},
  {id: 'gpu', title: 'GPU', label: 'idle: $0 / hour', x: 1170, y: 260, w: 250, h: 150, delay: 0.84, dx: 160, dy: 40, rot: 2.4},
  {id: 'credits', title: 'Credit', label: 'held / charged', x: 600, y: 456, w: 220, h: 130, delay: 0.98, dx: 100, dy: 130, rot: -1.8},
  {id: 'brain', title: 'Brain', label: 'TRIBE v2', x: 330, y: 456, w: 220, h: 130, delay: 1.12, dx: -40, dy: 140, rot: 1.5},
  {id: 'report', title: 'Report', label: 'Bedrock · Claude', x: 1170, y: 456, w: 250, h: 130, delay: 1.26, dx: 150, dy: 120, rot: -2},
  {id: 'response', title: 'Response', label: 'second by second', x: 880, y: 456, w: 250, h: 130, delay: 1.4, dx: 80, dy: 100, rot: 1.2},
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
    background: C.surface, borderRadius: 13, boxSizing: 'border-box',
    border: `1px solid ${hot ? C.hot : C.rule}`,
    boxShadow: hot ? `0 0 0 1px ${C.hot}, 0 0 34px rgba(255,138,42,.28)` : '0 12px 30px rgba(0,0,0,.35)',
    display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 18px',
    fontFamily, color: C.text,
  }}>
    <div style={{fontSize: 28, lineHeight: 1.1, fontWeight: 600, letterSpacing: '-0.025em'}}>{box.title}</div>
    <div style={{marginTop: 7, fontSize: 16, lineHeight: 1.3, color: C.muted, letterSpacing: '-0.005em'}}>{box.label}</div>
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
      position: 'absolute', left: 160, top: 110, width: 1600, height: 860,
      borderRadius: 20, boxSizing: 'border-box', border: `1px solid ${C.rule}`,
      opacity: frameIn, boxShadow: '0 30px 90px rgba(0,0,0,.6)',
    }} />
    <div style={{
      position: 'absolute', left: 198, top: 146, opacity: frameIn,
      fontSize: 30, fontWeight: 600, letterSpacing: '-0.03em',
    }}>Terraform</div>
    <div style={{
      position: 'absolute', left: 198, top: 184, opacity: frameIn,
      fontSize: 18, color: C.muted, letterSpacing: '-0.005em',
    }}>one command up, one command down</div>

    <div style={{position: 'absolute', left: 160, top: 110, width: 1600, height: 860}}>
      <svg width={1600} height={860} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        <defs>
          <marker id="built-arrow" viewBox="0 0 10 10" refX="8" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#4B515A" />
          </marker>
        </defs>
        <path d="M 280 335 L 320 335" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 550 335 L 590 335" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 820 335 L 870 335" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 1130 335 L 1160 335" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 170 400 L 170 450" fill="none" stroke="#4B515A" strokeWidth={2} strokeDasharray="8 9" opacity={frameIn * 0.7} />
        <path d="M 1005 400 L 710 450" fill="none" stroke="#4B515A" strokeWidth={2} strokeDasharray="8 9" opacity={frameIn * 0.7} />
        <path d="M 550 521 L 870 521" fill="none" stroke="#4B515A" strokeWidth={2} opacity={frameIn * 0.7} />
        <path d="M 1295 410 L 1295 450" fill="none" stroke="#4B515A" strokeWidth={2} markerEnd="url(#built-arrow)" opacity={frameIn} />
        <path d="M 710 780 L 1295 780 L 1295 410" fill="none" stroke="#FF8A2A"
          strokeWidth={2} strokeDasharray="8 9" opacity={watch * 0.75} />
      </svg>

      {boxes.map((box) => <Card key={box.id} box={box} t={t} />)}

      <div style={{
        position: 'absolute', left: 600, top: 650, width: 220, height: 130,
        opacity: watch, transform: `translateY(${(1 - watch) * 14}px)`,
        background: C.surface, borderRadius: 13, border: `1px solid ${C.rule}`,
        boxShadow: '0 12px 30px rgba(0,0,0,.35)',
        display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 18px',
        fontFamily, color: C.text,
      }}>
        <div style={{fontSize: 28, fontWeight: 600, letterSpacing: '-0.025em'}}>Watchdog</div>
        <div style={{marginTop: 7, fontSize: 16, color: C.muted}}>every 10 min</div>
        <svg width={34} height={28} viewBox="0 0 34 28" style={{
          position: 'absolute', right: 16, bottom: 14, overflow: 'visible',
        }}>
          <polyline points="3,15 12,24 31,4" fill="none" stroke={C.hot} strokeWidth={3}
            strokeLinecap="round" strokeLinejoin="round"
            strokeDasharray={checkLen} strokeDashoffset={checkLen * (1 - tick)} opacity={tick > 0 ? 1 : 0} />
        </svg>
      </div>

      <div style={{
        position: 'absolute', left: 1170, top: 260, width: 250, height: 150,
        borderRadius: 13, pointerEvents: 'none',
        background: `rgba(0,0,0,${gpuDim * 0.24})`,
      }} />
      <div style={{
        position: 'absolute', left: 1188, top: 376, fontFamily, fontSize: 15,
        color: C.muted, opacity: gpuDim,
      }}>off · $0 / hour</div>
    </div>
  </AbsoluteFill>;
};
