import React from 'react';
import {Easing, useCurrentFrame} from 'remotion';
import {Crop, cropAt, project} from './camera';
import {
  CAMS, CLICKS, CURSOR_KEYS, FPS, Key, Point, STAGE, at, playheadCursor,
} from './timing';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const bezier = (a: Point, b: Point, p: number): Point => {
  if (!b.bow) return {x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p};
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const cx = (a.x + b.x) / 2 - (dy / len) * b.bow;
  const cy = (a.y + b.y) / 2 + (dx / len) * b.bow;
  const q = 1 - p;
  return {
    x: q * q * a.x + 2 * q * p * cx + p * p * b.x,
    y: q * q * a.y + 2 * q * p * cy + p * p * b.y,
  };
};

const cursorSource = (t: number): Point => {
  if (t < at(9)) return playheadCursor(t);
  let previous: Key = CURSOR_KEYS[0];
  for (let i = 1; i < CURSOR_KEYS.length; i++) {
    const next = CURSOR_KEYS[i];
    if (t < next.at) {
      const p = Easing.inOut(Easing.cubic)(clamp((t - previous.at) / (next.at - previous.at)));
      return bezier(previous, next, p);
    }
    previous = next;
  }
  return previous;
};

const Arrow: React.FC<{crop: Crop; t: number}> = ({crop, t}) => {
  const source = cursorSource(t);
  const {x, y} = project(source, crop, STAGE);

  let press = 1;
  let ripple = 0;
  for (const click of CLICKS) {
    const dt = t - click.at;
    if (Math.abs(dt) < 0.12) press = Math.min(press, 1 - 0.1 * (1 - Math.abs(dt) / 0.12));
    if (dt >= 0 && dt < 0.45) ripple = Math.max(ripple, 1 - dt / 0.45);
  }

  const radius = 8 + (1 - ripple) * 64;
  return <>
    {ripple > 0 ? <div style={{
      position: 'absolute',
      left: x - radius,
      top: y - radius,
      width: radius * 2,
      height: radius * 2,
      borderRadius: 999,
      border: '2px solid rgba(255,138,42,.8)',
      opacity: ripple * 0.55,
    }} /> : null}
    <svg
      width={36}
      height={44}
      viewBox="0 0 20 24"
      style={{
        position: 'absolute',
        left: x - 2,
        top: y - 2,
        transform: `scale(${press})`,
        transformOrigin: '0 0',
        filter: 'drop-shadow(0 5px 10px rgba(0,0,0,.55))',
      }}
    >
      <path
        d="M2 2 L2 20 L6.7 15.4 L10 23 L13.2 21.6 L9.9 14.3 L16.6 14.3 Z"
        fill="#ECEDEF"
        stroke="#0A0B0D"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
    </svg>
  </>;
};

export const Cursor: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const crop = cropAt(CAMS, t);
  return <Arrow crop={crop} t={t} />;
};
