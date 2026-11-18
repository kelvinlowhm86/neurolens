import React from 'react';
import {Easing, Img, interpolate, staticFile} from 'remotion';

export type Pt = {x: number; y: number};

export const CLIP_SRC = 'walkthrough/swee/frames/f15.jpg';

export const ClipArt: React.FC<{
  width: number;
  radius?: number;
  border?: string;
  style?: React.CSSProperties;
}> = ({width, radius, border, style}) => (
  <div style={{
    width,
    height: (width * 16) / 9,
    borderRadius: radius ?? Math.round(width * 0.1),
    overflow: 'hidden',
    border,
    boxSizing: 'border-box',
    ...style,
  }}>
    <Img
      src={staticFile(CLIP_SRC)}
      style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}}
    />
  </div>
);

export const flight = (f: number, a = 4, b = 56) =>
  interpolate(f, [a, b], [0.04, 0.96], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

export const bez = (a: Pt, c: Pt, b: Pt, t: number): Pt => {
  const u = 1 - t;
  return {
    x: u * u * a.x + 2 * u * t * c.x + t * t * b.x,
    y: u * u * a.y + 2 * u * t * c.y + t * t * b.y,
  };
};

export const bezAngle = (a: Pt, c: Pt, b: Pt, t: number) => {
  const u = 1 - t;
  const dx = 2 * u * (c.x - a.x) + 2 * t * (b.x - c.x);
  const dy = 2 * u * (c.y - a.y) + 2 * t * (b.y - c.y);
  return (Math.atan2(dy, dx) * 180) / Math.PI;
};

export const trail = (a: Pt, c: Pt, b: Pt, t: number, count: number, span: number) =>
  Array.from({length: count}, (_, i) => {
    const tt = Math.max(0, t - (span * (i + 1)) / count);
    return {p: bez(a, c, b, tt), k: 1 - (i + 1) / (count + 1)};
  });

export const pathD = (a: Pt, c: Pt, b: Pt, samples = 56) =>
  Array.from({length: samples + 1}, (_, i) => {
    const p = bez(a, c, b, i / samples);
    return `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`;
  }).join(' ');

export const arrowD = (tip: Pt, angleDeg: number, size = 13) => {
  const a = (angleDeg * Math.PI) / 180;
  const p1 = {x: tip.x - size * Math.cos(a - 0.42), y: tip.y - size * Math.sin(a - 0.42)};
  const p2 = {x: tip.x - size * Math.cos(a + 0.42), y: tip.y - size * Math.sin(a + 0.42)};
  return `M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} L ${tip.x.toFixed(2)} ${tip.y.toFixed(2)} L ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
};

export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
