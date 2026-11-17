import React from 'react';
import {Easing, interpolate, interpolateColors, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {clamp, ease, enter} from '../edit/anim';

export type Pt = {x: number; y: number};
export type BoxState = 'hidden' | 'idle' | 'lit' | 'dim';

export const BOX_W = 250;
export const BOX_H = 150;
export const DANGER = '#E05A4F';

const easeInOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const mix = (a: string, b: string, t: number) => interpolateColors(t, [0, 1], [a, b]);

const LOOK: Record<'idle' | 'lit' | 'dim', {border: string; label: string; glow: number; opacity: number}> = {
  idle: {border: C.rule, label: '#8A9099', glow: 0, opacity: 0.74},
  lit: {border: 'rgba(255,138,42,.82)', label: C.text, glow: 1, opacity: 1},
  dim: {border: 'rgba(36,38,43,.55)', label: '#565B63', glow: 0, opacity: 0.42},
};

type BoxProps = {
  label: string;
  service?: string;
  x: number;
  y: number;
  state: BoxState;
  appear: number;
  stateAt?: number;
  prev?: BoxState;
};

export const Box: React.FC<BoxProps> = ({label, service, x, y, state, appear, stateAt = 0, prev}) => {
  const f = useCurrentFrame();
  const p = clamp(enter(f, appear, 18));
  if (state === 'hidden' && (prev === undefined || prev === 'hidden')) return null;
  const target = state === 'hidden' ? LOOK.dim : LOOK[state];
  const from = prev && prev !== 'hidden' ? LOOK[prev] : target;
  const t = stateAt > 0 ? ease(f, stateAt, stateAt + 10) : 1;
  const glow = from.glow + (target.glow - from.glow) * t;
  const opacity = (from.opacity + (target.opacity - from.opacity) * t) * (state === 'hidden' ? 1 - t : 1);
  const border = mix(from.border, target.border, t);
  const colour = mix(from.label, target.label, t);
  const appearOpacity = state === 'hidden' ? 1 : p;
  return <div style={{
    position: 'absolute',
    left: x,
    top: y,
    width: BOX_W,
    height: BOX_H,
    boxSizing: 'border-box',
    borderRadius: 28,
    background: C.surface,
    border: `1.5px solid ${border}`,
    boxShadow: glow > 0.01
      ? `0 0 0 1px rgba(255,138,42,${0.12 * glow}), 0 0 ${46 * glow}px rgba(255,138,42,${0.22 * glow}), inset 0 1px 0 rgba(255,255,255,.04)`
      : 'inset 0 1px 0 rgba(255,255,255,.03)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily,
    opacity: appearOpacity * opacity,
    transform: `translateY(${(1 - p) * 12}px) scale(${0.97 + p * 0.03})`,
  }}>
    <div style={{fontSize: 44, fontWeight: 600, letterSpacing: '-.02em', color: colour, lineHeight: 1}}>{label}</div>
    {service ? <div style={{fontSize: 26, fontWeight: 400, letterSpacing: '.02em', color: C.muted, marginTop: 10}}>{service}</div> : null}
  </div>;
};

export const left = (b: {x: number; y: number}): Pt => ({x: b.x, y: b.y + BOX_H / 2});
export const right = (b: {x: number; y: number}): Pt => ({x: b.x + BOX_W, y: b.y + BOX_H / 2});
export const top = (b: {x: number; y: number}): Pt => ({x: b.x + BOX_W / 2, y: b.y});
export const bottom = (b: {x: number; y: number}): Pt => ({x: b.x + BOX_W / 2, y: b.y + BOX_H});

export type PathSpec = {from: Pt; to: Pt; bend?: number};

const SAMPLES = 64;

const pointsOf = ({from, to, bend = 0}: PathSpec): Pt[] => {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const control = {
    x: (from.x + to.x) / 2 + (-dy / len) * bend,
    y: (from.y + to.y) / 2 + (dx / len) * bend,
  };
  return Array.from({length: SAMPLES + 1}, (_, i) => {
    const t = i / SAMPLES;
    const u = 1 - t;
    return {
      x: u * u * from.x + 2 * u * t * control.x + t * t * to.x,
      y: u * u * from.y + 2 * u * t * control.y + t * t * to.y,
    };
  });
};

const lengthsOf = (points: Pt[]) => {
  const out = [0];
  for (let i = 1; i < points.length; i += 1) {
    out.push(out[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
  }
  return out;
};

const pointAt = (points: Pt[], fractions: number[], t: number): Pt => {
  const total = fractions[fractions.length - 1] || 1;
  const target = clamp(t) * total;
  let i = 1;
  while (i < fractions.length - 1 && fractions[i] < target) i += 1;
  const span = fractions[i] - fractions[i - 1] || 1;
  const local = clamp((target - fractions[i - 1]) / span);
  const a = points[i - 1];
  const b = points[i];
  return {x: a.x + (b.x - a.x) * local, y: a.y + (b.y - a.y) * local};
};

const trim = (points: Pt[], fractions: number[], a: number, b: number): Pt[] =>
  Array.from({length: 25}, (_, i) => pointAt(points, fractions, a + ((b - a) * i) / 24));

const dOf = (points: Pt[]) =>
  points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');

const LINK = '#3D4148';

type LinkProps = {
  path: PathSpec;
  appear: number;
  variant?: 'solid' | 'dashed' | 'crossed';
  variantAt?: number;
  gap?: number;
};

export const Link: React.FC<LinkProps> = ({path, appear, variant = 'solid', variantAt, gap = 0}) => {
  const f = useCurrentFrame();
  const p = ease(f, appear, appear + 20);
  if (p <= 0) return null;
  const points = pointsOf(path);
  const fractions = lengthsOf(points);
  const total = fractions[fractions.length - 1] || 1;
  const segments = gap > 0.5
    ? [trim(points, fractions, 0, 0.5 - gap / 2 / total), trim(points, fractions, 0.5 + gap / 2 / total, 1)]
    : [points];
  const dashMix = variant === 'dashed' ? ease(f, variantAt ?? appear, (variantAt ?? appear) + 10) : 0;
  const draw = p < 1 ? {strokeDasharray: `${total}`, strokeDashoffset: `${total * (1 - p)}`} : {};
  const mid = pointAt(points, fractions, 0.5);
  return <g>
    {segments.map((segment, i) => <React.Fragment key={i}>
      <path d={dOf(segment)} fill="none" stroke={LINK} strokeWidth={2.5} strokeLinecap="round" {...draw} opacity={(variant === 'crossed' ? 0.55 : 1) * (1 - dashMix)} />
      {variant === 'dashed' ? <path d={dOf(segment)} fill="none" stroke={LINK} strokeWidth={2.5} strokeLinecap="round" strokeDasharray="12 14" opacity={dashMix} /> : null}
    </React.Fragment>)}
    {variant === 'crossed' && p > 0.7 ? <g stroke={DANGER} strokeWidth={4} strokeLinecap="round" opacity={p}>
      <path d={`M ${mid.x - 15} ${mid.y - 15} L ${mid.x + 15} ${mid.y + 15}`} />
      <path d={`M ${mid.x - 15} ${mid.y + 15} L ${mid.x + 15} ${mid.y - 15}`} />
    </g> : null}
  </g>;
};

type DotProps = {
  path: PathSpec;
  start: number;
  end: number;
  label?: string;
  color?: string;
  stopAt?: number;
  labelOffset?: Pt;
  linger?: boolean;
};

export const Dot: React.FC<DotProps> = ({path, start, end, label, color = C.hot, stopAt = 1, labelOffset, linger = false}) => {
  const f = useCurrentFrame();
  if (f < start - 1 || (f > end + 10 && !linger)) return null;
  const points = pointsOf(path);
  const fractions = lengthsOf(points);
  const travel = easeInOut(f, start, end) * stopAt;
  const at = pointAt(points, fractions, travel);
  const opacity = Math.min(clamp((f - start + 1) / 3), linger ? 1 : 1 - clamp((f - end) / 10));
  if (opacity <= 0) return null;
  const labelFade = label ? (linger ? clamp((f - start) / 6) : Math.min(clamp((f - start) / 6), 1 - ease(f, end - (end - start) * 0.25, end))) : 0;
  const lw = label ? label.length * 15.5 + 34 : 0;
  const off = labelOffset ?? {x: 0, y: -66};
  const lx = clamp(at.x + off.x - lw / 2, 30, 1890 - lw);
  const ly = at.y + off.y;
  return <g opacity={opacity}>
    <circle cx={at.x} cy={at.y} r={26} fill={color} opacity={0.14} />
    <circle cx={at.x} cy={at.y} r={16} fill={color} opacity={0.3} />
    <circle cx={at.x} cy={at.y} r={9} fill={color} />
    {label && labelFade > 0.01 ? <g opacity={labelFade}>
      <rect x={lx} y={ly} width={lw} height={48} rx={14} fill="#0B0C0F" stroke={C.rule} strokeWidth={1.5} />
      <text x={lx + lw / 2} y={ly + 31} textAnchor="middle" fontFamily={fontFamily} fontSize={28} fontWeight={500} fill={C.text}>{label}</text>
    </g> : null}
  </g>;
};

export const Gate: React.FC<{x: number; y: number; closed: number; cross: number}> = ({x, y, closed, cross}) => {
  const open = 1 - clamp(closed);
  const lift = open * 34;
  return <g>
    <path d={`M ${x - 17} ${y - 42} L ${x - 17} ${y + 42}`} stroke={LINK} strokeWidth={3} strokeLinecap="round" opacity={0.5} />
    <path d={`M ${x + 17} ${y - 42} L ${x + 17} ${y + 42}`} stroke={LINK} strokeWidth={3} strokeLinecap="round" opacity={0.5} />
    <g transform={`translate(${x} ${y - lift})`} opacity={clamp(closed * 2)}>
      <rect x={-5} y={-38} width={10} height={76} rx={5} fill="#4A4E55" />
      <rect x={-5} y={-38} width={10} height={76} rx={5} fill={C.muted} opacity={0.35} />
    </g>
    {cross > 0.01 ? <g opacity={clamp(cross)}>
      <circle cx={x} cy={y} r={21} fill="#0B0C0F" opacity={0.92} />
      <g stroke={DANGER} strokeWidth={5} strokeLinecap="round">
        <path d={`M ${x - 9} ${y - 9} L ${x + 9} ${y + 9}`} />
        <path d={`M ${x - 9} ${y + 9} L ${x + 9} ${y - 9}`} />
      </g>
    </g> : null}
  </g>;
};

export const Caption: React.FC<{text: string; from: number; to: number; y?: number}> = ({text, from, to, y = 942}) => {
  const f = useCurrentFrame();
  const opacity = ease(f, from, from + 10) * (1 - ease(f, to - 10, to));
  if (opacity <= 0) return null;
  return <div style={{
    position: 'absolute',
    left: 0,
    right: 0,
    top: y,
    display: 'flex',
    justifyContent: 'center',
    pointerEvents: 'none',
  }}>
    <div style={{
      fontFamily,
      fontSize: 36,
      fontWeight: 600,
      letterSpacing: '.005em',
      color: C.hot,
      background: 'rgba(8,9,11,.74)',
      border: `1px solid ${C.rule}`,
      borderRadius: 999,
      padding: '14px 34px 16px',
      opacity,
      transform: `translateY(${(1 - ease(f, from, from + 10)) * 8}px)`,
    }}>{text}</div>
  </div>;
};
