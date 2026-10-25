import React, {useMemo} from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {f} from '../beats';
import {clamp} from './anim';
import {C} from './ui';

type Shard = {
  poly: string;
  cx: number;
  cy: number;
  ox: number;
  oy: number;
  speed: number;
  rot: number;
};

const COLS = 16;
const ROWS = 8;

const seeded = (seed: number) => {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
};

const build = (seed: number): Shard[] => {
  const rand = seeded(seed);
  const xs = Array.from({length: COLS + 1}, (_, i) => i / COLS);
  const ys = Array.from({length: ROWS + 1}, (_, i) => i / ROWS);
  for (let i = 1; i < COLS; i++) xs[i] += (rand() - .5) * (.45 / COLS);
  for (let i = 1; i < ROWS; i++) ys[i] += (rand() - .5) * (.55 / ROWS);
  const shards: Shard[] = [];
  for (let j = 0; j < ROWS; j++) for (let i = 0; i < COLS; i++) {
    const pts = [[xs[i], ys[j]], [xs[i + 1], ys[j]], [xs[i + 1], ys[j + 1]], [xs[i], ys[j + 1]]];
    const cx = (pts[0][0] + pts[1][0] + pts[2][0] + pts[3][0]) / 4;
    const cy = (pts[0][1] + pts[1][1] + pts[2][1] + pts[3][1]) / 4;
    const grow = 1.03;
    const poly = pts.map(([x, y]) => `${((cx + (x - cx) * grow) * 100).toFixed(2)}% ${((cy + (y - cy) * grow) * 100).toFixed(2)}%`).join(', ');
    const angle = Math.atan2((cy - .5) * 1.6, cx - .5) + (rand() - .5) * .9;
    shards.push({
      poly, cx, cy, ox: Math.cos(angle), oy: Math.sin(angle),
      speed: .7 + rand() * 1.9, rot: (rand() - .5) * 220,
    });
  }
  return shards;
};

type Props = {
  text: string;
  at: number;
  offset: number;
  size: number;
  y: number;
  color?: string;
  weight?: number;
  letterSpacing?: number;
  lineHeight?: number;
  textShadow?: string;
};

export const Shatter: React.FC<Props> = ({
  text, at, offset, size, y, color = C.text, weight = 600,
  letterSpacing = -.055, lineHeight = 1.03,
  textShadow = '0 8px 42px rgba(0,0,0,.62)',
}) => {
  const g = useCurrentFrame() + offset;
  const seed = useMemo(
    () => text.split('').reduce((sum, ch) => sum + ch.charCodeAt(0), 0) + size * 31,
    [text, size],
  );
  const shards = useMemo(() => build(seed), [seed]);
  const life = f(.72);
  const p = clamp((g - at) / life);
  const baseVisible = g < at;
  const shardsVisible = g >= at;
  const beat = p;
  const opacity = 1 - clamp((p - .35) / .65);
  const textStyle: React.CSSProperties = {
    position: 'absolute', inset: 0, textAlign: 'center', whiteSpace: 'nowrap',
    fontSize: size, lineHeight, fontWeight: weight, letterSpacing: `${letterSpacing}em`,
    color, textShadow, fontVariantNumeric: 'tabular-nums',
  };
  return <div style={{position: 'absolute', left: 0, right: 0, top: y, transform: 'translateY(-50%)', display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
    <div style={{position: 'relative', display: 'inline-block', whiteSpace: 'nowrap', fontSize: size, lineHeight, fontWeight: weight, letterSpacing: `${letterSpacing}em`, color, textShadow, fontVariantNumeric: 'tabular-nums'}}>
      <div style={{opacity: baseVisible ? 1 : 0}}>{text}</div>
      <AbsoluteFill style={{opacity: shardsVisible ? opacity : 0}}>
        {shards.map((shard, i) => {
          const dx = shard.ox * shard.speed * size * 1.35 * beat;
          const dy = shard.oy * shard.speed * size * 1.35 * beat;
          const rot = shard.rot * beat;
          const scale = 1 + .16 * beat;
          return <div key={i} style={{
            position: 'absolute', inset: 0, clipPath: `polygon(${shard.poly})`,
            transformOrigin: `${shard.cx * 100}% ${shard.cy * 100}%`,
            transform: `translate(${dx}px, ${dy}px) rotate(${rot}deg) scale(${scale})`,
            filter: `blur(${5.5 * beat}px)`,
          }}>
            <div style={textStyle}>{text}</div>
          </div>;
        })}
      </AbsoluteFill>
    </div>
  </div>;
};
