import React from 'react';
import {Easing, interpolate, interpolateColors, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {clamp, ease} from '../edit/anim';
import {ClipArt} from './styles/shared';

export type Pt = {x: number; y: number};
export type NodeState = 'hidden' | 'idle' | 'lit' | 'dim' | 'off';

export const DANGER = '#E05A4F';
export const ISO_ANGLE = -28;

const smooth = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

const mix = (a: string, b: string, t: number) => interpolateColors(t, [0, 1], [a, b]);

type Look = {border: string; icon: string; label: string; glow: number; opacity: number};

const LOOK: Record<Exclude<NodeState, 'hidden'>, Look> = {
  idle: {
    border: 'rgba(150,168,190,.62)',
    icon: '#ACBCCE',
    label: '#D6DCE4',
    glow: 0.16,
    opacity: 0.96,
  },
  lit: {
    border: 'rgba(255,138,42,.78)',
    icon: '#FFC08A',
    label: C.text,
    glow: 1,
    opacity: 1,
  },
  dim: {
    border: 'rgba(70,78,90,.5)',
    icon: '#5D6672',
    label: '#6B727C',
    glow: 0.04,
    opacity: 0.46,
  },
  off: {
    border: 'rgba(52,58,67,.55)',
    icon: '#4B535D',
    label: '#555C65',
    glow: 0,
    opacity: 0.3,
  },
};

export type IconName =
  | 'browser'
  | 'website'
  | 'storage'
  | 'queue'
  | 'check'
  | 'gpu'
  | 'database'
  | 'model'
  | 'watchdog'
  | 'deadLetter'
  | 'terraform'
  | 'key'
  | 'clock'
  | 'speaker'
  | 'muted'
  | 'tick'
  | 'cross';

const iconDots = (
  <>
    <circle cx="15" cy="18.5" r="1.7" fill="currentColor" stroke="none" />
    <circle cx="21" cy="18.5" r="1.7" fill="currentColor" stroke="none" opacity=".65" />
  </>
);

const IconArt: Record<IconName, React.ReactNode> = {
  browser: (
    <>
      <rect x="7" y="13" width="50" height="38" rx="8" />
      <path d="M7 24H57" />
      {iconDots}
      <circle cx="32" cy="38" r="8" />
      <ellipse cx="32" cy="38" rx="3.4" ry="8" />
      <path d="M24 38H40" />
      <path d="M42 33l8 4-3.4 1.2-1.2 3.4z" />
    </>
  ),
  website: (
    <>
      <rect x="7" y="12" width="50" height="40" rx="7" />
      <path d="M7 22H57" />
      {iconDots}
      <path d="M13 22V52M13 29H57" />
      <rect x="17" y="33" width="15" height="13" rx="2" />
      <path d="M37 33H53M37 39H53M37 45H46" opacity=".8" />
    </>
  ),
  storage: (
    <>
      <path d="M13 20H51L47 52H17Z" />
      <ellipse cx="32" cy="20" rx="19" ry="6" />
      <path d="M22 30H42M22 37H36" opacity=".75" />
      <path d="M18 46C22 48 26 49 32 49C38 49 42 48 46 46" opacity=".45" />
    </>
  ),
  queue: (
    <>
      <rect x="12" y="14" width="40" height="9" rx="4.5" />
      <rect x="16" y="27.5" width="32" height="9" rx="4.5" opacity=".8" />
      <rect x="20" y="41" width="24" height="9" rx="4.5" opacity=".6" />
    </>
  ),
  check: (
    <>
      <path d="M32 8l18 7v14c0 12-8 20-18 25-10-5-18-13-18-25V15z" />
      <path d="M23 32l6 6 12-13" />
    </>
  ),
  gpu: (
    <>
      <rect x="17" y="17" width="30" height="30" rx="5" />
      <rect x="25" y="25" width="14" height="14" rx="2" opacity=".5" />
      <path d="M24 17V10M32 17V10M40 17V10M24 54v-7M32 54v-7M40 54v-7M17 24H10M17 32H10M17 40H10M54 24h-7M54 32h-7M54 40h-7" />
    </>
  ),
  database: (
    <>
      <ellipse cx="32" cy="14" rx="18" ry="6" />
      <path d="M14 14v12c0 3.3 8.1 6 18 6s18-2.7 18-6V14" />
      <path d="M14 26v12c0 3.3 8.1 6 18 6s18-2.7 18-6V26" />
      <path d="M14 38v12c0 3.3 8.1 6 18 6s18-2.7 18-6V38" />
    </>
  ),
  model: (
    <>
      <path d="M30 12c-4-1-8 1-9 5-4 1-6 5-5 9-3 3-2 8 1 10-1 4 2 8 6 8 1 3 4 5 8 4V12z" />
      <path d="M34 12c4-1 8 1 9 5 4 1 6 5 5 9 3 3 2 8-1 10 1 4-2 8-6 8-1 3-4 5-8 4V12z" />
      <path d="M32 16v30" opacity=".45" />
      <path d="M23 27c2-2 5-1 7 1M41 27c-2-2-5-1-7 1M22 39c3-2 6-1 8 1M42 39c-3-2-6-1-8 1" opacity=".55" />
    </>
  ),
  watchdog: (
    <>
      <path d="M8 32c6-9 15-14 24-14s18 5 24 14c-6 9-15 14-24 14S14 41 8 32z" />
      <circle cx="32" cy="32" r="8" />
      <circle cx="32" cy="32" r="2.6" fill="currentColor" stroke="none" />
    </>
  ),
  deadLetter: (
    <>
      <path d="M14 34l4-18h28l4 18" />
      <path d="M14 34h11l2 5h10l2-5h11v10c0 3-2 5-5 5H19c-3 0-5-2-5-5z" />
      <path d="M32 12v14M27 21l5 5 5-5" />
    </>
  ),
  terraform: (
    <>
      <path d="M22 18L12 32l10 14" />
      <path d="M42 18l10 14-10 14" />
      <path d="M36 13l-8 38" opacity=".65" />
    </>
  ),
  key: (
    <>
      <circle cx="21" cy="32" r="8" />
      <path d="M29 32h25M45 32v8M52 32v6" />
    </>
  ),
  clock: (
    <>
      <circle cx="32" cy="32" r="20" />
      <path d="M32 20v13l9 6" />
    </>
  ),
  speaker: (
    <>
      <path d="M12 26h8l10-8v28l-10-8h-8z" />
      <path d="M37 25c3 3 3 11 0 14M42 21c6 6 6 16 0 22" />
    </>
  ),
  muted: (
    <>
      <path d="M12 26h8l10-8v28l-10-8h-8z" />
      <path d="M38 27l12 12M50 27L38 39" />
    </>
  ),
  tick: <path d="M14 34l11 11 25-27" />,
  cross: <path d="M18 18l28 28M46 18L18 46" />,
};

export const Icon: React.FC<{name: IconName; size?: number}> = ({name, size = 64}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 64 64"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.2}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{display: 'block'}}
  >
    {IconArt[name]}
  </svg>
);

export type StateKey = {at: number; state: NodeState};

export const stateAt = (f: number, keys: StateKey[]) => {
  let index = -1;
  for (let i = 0; i < keys.length; i += 1) if (keys[i].at <= f) index = i;
  if (index < 0) return {state: 'hidden' as NodeState, at: 0, prev: undefined};
  return {
    state: keys[index].state,
    at: keys[index].at,
    prev: index > 0 ? keys[index - 1].state : undefined,
  };
};

type NodeProps = {
  x: number;
  y: number;
  label: string;
  service: string;
  icon: IconName;
  state: NodeState;
  stateAt: number;
  prev?: NodeState;
  appear: number;
  dx?: number;
  keyAt?: number;
  keySide?: 'left' | 'right';
  wide?: boolean;
  showService?: boolean;
  serviceOpacity?: number;
};

export const Node: React.FC<NodeProps> = ({
  x,
  y,
  label,
  service,
  icon,
  state,
  stateAt: stateFrame,
  prev,
  appear,
  dx = 0,
  keyAt,
  keySide = 'right',
  wide = false,
  showService = true,
  serviceOpacity = 1,
}) => {
  const f = useCurrentFrame();
  const rise = ease(f, appear, appear + 18);
  if (state === 'hidden' && (prev === undefined || prev === 'hidden') && !keyAt) return null;
  const target = state === 'hidden' ? LOOK.dim : LOOK[state];
  const from = prev && prev !== 'hidden' ? LOOK[prev] : target;
  const t = prev === undefined && state !== 'hidden' ? 1 : smooth(f, stateFrame, stateFrame + 10);
  const glow = from.glow + (target.glow - from.glow) * t;
  const opacity = (from.opacity + (target.opacity - from.opacity) * t) * (state === 'hidden' ? 1 - t : 1) * rise;
  const border = mix(from.border, target.border, t);
  const iconColour = mix(from.icon, target.icon, t);
  const labelColour = mix(from.label, target.label, t);
  const wideLabel = wide && state !== 'dim' && state !== 'off' ? mix(labelColour, '#F2F5F8', 0.5) : labelColour;
  const keyP = keyAt === undefined ? 0 : smooth(f, keyAt, keyAt + 10);
  const keyX = keySide === 'right' ? 54 : -54;
  if (opacity <= 0.001) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: x + dx - 190,
        top: y - 64,
        width: 380,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        fontFamily,
        opacity,
        transform: `translateY(${(1 - rise) * 20}px)`,
      }}
    >
      <div style={{position: 'relative', width: 128, height: 128}}>
        <div
          style={{
            position: 'absolute',
            left: 4,
            top: 108,
            width: 120,
            height: 28,
            borderRadius: '50%',
            background: `radial-gradient(ellipse at center, rgba(255,138,42,${0.2 * glow}), rgba(120,138,160,${0.07 * (1 - glow)}) 52%, transparent 72%)`,
            filter: 'blur(9px)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 16,
            top: 122,
            width: 96,
            height: 12,
            borderRadius: '50%',
            border: `1px solid rgba(${glow > 0.4 ? '255,158,84' : '150,168,190'},${0.18 + 0.12 * glow})`,
            background: glow > 0.3 ? 'rgba(255,138,42,.06)' : 'transparent',
          }}
        />
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: iconColour,
            background:
              glow > 0.06
                ? `radial-gradient(circle at 50% 42%, rgba(255,138,42,${0.22 * glow}) 0%, rgba(255,138,42,${0.06 * glow}) 54%, transparent 72%)`
                : 'radial-gradient(circle at 50% 42%, rgba(120,138,160,.13) 0%, rgba(120,138,160,.035) 54%, transparent 72%)',
            border: `2px solid ${border}`,
            boxShadow:
              glow > 0.06
                ? `0 0 ${48 * glow}px rgba(255,138,42,${0.2 * glow}), inset 0 0 26px rgba(255,138,42,${0.09 * glow})`
                : '0 0 30px rgba(120,138,160,.07), inset 0 0 22px rgba(120,138,160,.04)',
          }}
        >
          <Icon name={icon} size={78} />
        </div>
        {keyP > 0.01 ? (
          <div
            style={{
              position: 'absolute',
              left: 64 + keyX - 29,
              top: -8,
              width: 58,
              height: 58,
              borderRadius: 29,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(10,12,15,.94)',
              border: '1.5px solid rgba(255,158,84,.7)',
              boxShadow: '0 0 22px rgba(255,138,42,.3)',
              color: '#FFC08A',
              opacity: keyP,
              transform: `scale(${0.8 + 0.2 * keyP})`,
            }}
          >
            <Icon name="key" size={36} />
          </div>
        ) : null}
      </div>
      <div
        style={{
          marginTop: 16,
          fontSize: wide ? 64 : 58,
          fontWeight: wide ? 700 : 600,
          letterSpacing: '-.025em',
          lineHeight: 1,
          color: wideLabel,
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </div>
      {showService && serviceOpacity > 0.01 ? (
        <div
          style={{
            marginTop: 12,
            fontSize: 42,
            letterSpacing: '.04em',
            lineHeight: 1,
            color: mix('#9AA3AE', '#C9A17F', glow),
            whiteSpace: 'nowrap',
            opacity: serviceOpacity,
          }}
        >
          {service}
        </div>
      ) : null}
    </div>
  );
};

export type Pool = {x: number; y: number; rx?: number; ry?: number; color?: string; opacity?: number};

export const Floor: React.FC<{cx?: number; cy?: number; size?: number; pools?: Pool[]}> = ({
  cx = 1330,
  cy = 710,
  size = 3900,
  pools = [],
}) => (
  <>
    <div
      style={{
        position: 'absolute',
        left: cx,
        top: cy,
        width: size,
        height: size,
        transform: 'translate(-50%, -50%) rotateX(58deg) rotateZ(-45deg)',
        backgroundImage:
          'linear-gradient(rgba(139,166,194,.12) 1px, transparent 1px), linear-gradient(90deg, rgba(139,166,194,.12) 1px, transparent 1px)',
        backgroundSize: '84px 84px',
        border: '1px solid rgba(139,166,194,.09)',
        borderRadius: 90,
        maskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.85) 44%, transparent 76%)',
        WebkitMaskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.85) 44%, transparent 76%)',
      }}
    />
    <div
      style={{
        position: 'absolute',
        left: cx,
        top: cy,
        width: 2100,
        height: 1250,
        transform: 'translate(-50%, -50%)',
        borderRadius: '50%',
        background: 'radial-gradient(ellipse at center, rgba(66,88,118,.2), transparent 66%)',
        filter: 'blur(30px)',
      }}
    />
    {pools.map((p, i) => {
      const rx = p.rx ?? 210;
      const ry = p.ry ?? 95;
      return (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: p.x - rx,
            top: p.y - ry,
            width: rx * 2,
            height: ry * 2,
            borderRadius: '50%',
            background: `radial-gradient(ellipse at center, ${p.color ?? 'rgba(255,138,42,.24)'}, transparent 68%)`,
            opacity: p.opacity ?? 0.6,
            filter: 'blur(20px)',
            transform: `rotate(${ISO_ANGLE}deg)`,
          }}
        />
      );
    })}
  </>
);

export type PathSpec = {from: Pt; to: Pt; bend?: number; control?: Pt};

const SAMPLES = 64;

const pointsOf = ({from, to, bend = 0, control}: PathSpec): Pt[] => {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const ctrl =
    control ??
    {
      x: (from.x + to.x) / 2 + (-dy / len) * bend,
      y: (from.y + to.y) / 2 + (dx / len) * bend,
    };
  return Array.from({length: SAMPLES + 1}, (_, i) => {
    const t = i / SAMPLES;
    const u = 1 - t;
    return {
      x: u * u * from.x + 2 * u * t * ctrl.x + t * t * to.x,
      y: u * u * from.y + 2 * u * t * ctrl.y + t * t * to.y,
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

export const pathPoint = (path: PathSpec, t: number): Pt => {
  const points = pointsOf(path);
  return pointAt(points, lengthsOf(points), t);
};

export const pathAngle = (path: PathSpec, t: number): number => {
  const points = pointsOf(path);
  const fractions = lengthsOf(points);
  const a = pointAt(points, fractions, Math.max(0, t - 0.01));
  const b = pointAt(points, fractions, Math.min(1, t + 0.01));
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
};

export const arrowD = (tip: Pt, angleDeg: number, size = 13) => {
  const a = (angleDeg * Math.PI) / 180;
  const p1 = {x: tip.x - size * Math.cos(a - 0.42), y: tip.y - size * Math.sin(a - 0.42)};
  const p2 = {x: tip.x - size * Math.cos(a + 0.42), y: tip.y - size * Math.sin(a + 0.42)};
  return `M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} L ${tip.x.toFixed(2)} ${tip.y.toFixed(2)} L ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
};

type LinkProps = {
  path: PathSpec;
  appear: number;
  draw?: number;
  dashed?: boolean;
  dashedAt?: number;
  gap?: number;
  hot?: number;
  colour?: string;
  width?: number;
  arrow?: boolean;
  blocked?: number;
  opacity?: number;
};

const LINK_COLOUR = '#46536A';

export const Link: React.FC<LinkProps> = ({
  path,
  appear,
  draw = 20,
  dashed = false,
  dashedAt,
  gap = 0,
  hot = 0,
  colour = LINK_COLOUR,
  width = 2.6,
  arrow = false,
  blocked = 0,
  opacity = 1,
}) => {
  const f = useCurrentFrame();
  const p = ease(f, appear, appear + draw);
  if (p <= 0) return null;
  const points = pointsOf(path);
  const fractions = lengthsOf(points);
  const total = fractions[fractions.length - 1] || 1;
  const segments =
    gap > 0.5
      ? [trim(points, fractions, 0, 0.5 - gap / 2 / total), trim(points, fractions, 0.5 + gap / 2 / total, 1)]
      : [points];
  const dashP = dashed ? (dashedAt === undefined ? 1 : ease(f, dashedAt, dashedAt + 12)) : 0;
  const hotP = clamp(hot);
  const end = pointAt(points, fractions, 1);
  const mid = pointAt(points, fractions, 0.62);
  const drawProps = {pathLength: 1, strokeDasharray: '1', strokeDashoffset: 1 - p} as const;
  return (
    <g opacity={opacity}>
      {hotP > 0.01
        ? segments.map((segment, i) => (
            <path
              key={`glow${i}`}
              d={dOf(segment)}
              fill="none"
              stroke="rgba(255,138,42,.58)"
              strokeWidth={width * 5}
              strokeLinecap="round"
              opacity={hotP * 0.55}
              filter="url(#v6-glow)"
              {...drawProps}
            />
          ))
        : null}
      {segments.map((segment, i) => (
        <path
          key={`base${i}`}
          d={dOf(segment)}
          fill="none"
          stroke={mix(colour, '#FFB47C', hotP)}
          strokeWidth={width}
          strokeLinecap="round"
          opacity={1 - dashP * 0.82}
          {...drawProps}
        />
      ))}
      {dashP > 0.01
        ? segments.map((segment, i) => (
            <path
              key={`dash${i}`}
              d={dOf(segment)}
              fill="none"
              stroke={mix('#8B9DB4', '#FFB47C', hotP)}
              strokeWidth={width + 0.2}
              strokeLinecap="round"
              strokeDasharray="13 15"
              opacity={dashP}
            />
          ))
        : null}
      {arrow && p > 0.92 ? (
        <path
          d={arrowD(end, pathAngle(path, 1), 13)}
          fill="none"
          stroke={mix('#7F92AA', '#FFB47C', hotP)}
          strokeWidth={width}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={clamp((p - 0.92) / 0.08)}
        />
      ) : null}
      {blocked > 0.01 ? (
        <g opacity={blocked}>
          <rect x={mid.x - 13} y={mid.y - 46} width={26} height={92} rx={13} fill="#0B0C0F" stroke={DANGER} strokeWidth={3} />
          <rect x={mid.x - 5} y={mid.y - 38} width={10} height={76} rx={5} fill={DANGER} opacity={0.7} />
          <circle cx={mid.x} cy={mid.y} r={38} fill="#0B0C0F" opacity={0.9} />
          <g stroke={DANGER} strokeWidth={7} strokeLinecap="round">
            <path d={`M ${mid.x - 16} ${mid.y - 16} L ${mid.x + 16} ${mid.y + 16}`} />
            <path d={`M ${mid.x - 16} ${mid.y + 16} L ${mid.x + 16} ${mid.y - 16}`} />
          </g>
        </g>
      ) : null}
    </g>
  );
};

export const Gate: React.FC<{x: number; y: number; closed: number; cross: number}> = ({x, y, closed, cross}) => {
  const open = 1 - clamp(closed);
  const lift = open * 36;
  return (
    <g>
      <path d={`M ${x - 17} ${y - 44} L ${x - 17} ${y + 44}`} stroke={LINK_COLOUR} strokeWidth={3} strokeLinecap="round" opacity={0.55} />
      <path d={`M ${x + 17} ${y - 44} L ${x + 17} ${y + 44}`} stroke={LINK_COLOUR} strokeWidth={3} strokeLinecap="round" opacity={0.55} />
      <g transform={`translate(${x} ${y - lift})`} opacity={clamp(closed * 2)}>
        <rect x={-6} y={-40} width={12} height={80} rx={6} fill="#4A4E55" />
        <rect x={-6} y={-40} width={12} height={80} rx={6} fill={C.muted} opacity={0.35} />
      </g>
      {cross > 0.01 ? (
        <g opacity={clamp(cross)}>
          <circle cx={x} cy={y} r={23} fill="#0B0C0F" opacity={0.92} />
          <g stroke={DANGER} strokeWidth={5} strokeLinecap="round">
            <path d={`M ${x - 10} ${y - 10} L ${x + 10} ${y + 10}`} />
            <path d={`M ${x - 10} ${y + 10} L ${x + 10} ${y - 10}`} />
          </g>
        </g>
      ) : null}
    </g>
  );
};

type TravellerProps = {
  path: PathSpec;
  start: number;
  end: number;
  kind?: 'clip' | 'job' | 'dot';
  colour?: string;
  stopAt?: number;
  linger?: boolean;
  outAt?: number;
  subLabel?: string;
  trail?: number;
};

export const Traveller: React.FC<TravellerProps> = ({
  path,
  start,
  end,
  kind = 'dot',
  colour = C.hot,
  stopAt = 1,
  linger = false,
  outAt,
  subLabel,
  trail = 5,
}) => {
  const f = useCurrentFrame();
  if (f < start - 1) return null;
  const t = smooth(f, start, end) * stopAt;
  const at = pathPoint(path, t);
  const angle = pathAngle(path, t);
  const fadeIn = clamp((f - start + 1) / 5);
  const fadeOut = outAt !== undefined ? 1 - ease(f, outAt, outAt + 14) : linger ? 1 : 1 - ease(f, end + 4, end + 16);
  const opacity = Math.min(fadeIn, fadeOut);
  if (opacity <= 0.001) return null;
  const ghosts = Array.from({length: trail}, (_, i) => {
    const tt = Math.max(0, t - (0.22 * (i + 1)) / trail);
    return {p: pathPoint(path, tt), k: 1 - (i + 1) / (trail + 1)};
  });
  return (
    <g opacity={opacity}>
      <ellipse
        cx={at.x}
        cy={at.y + 74}
        rx={150}
        ry={66}
        fill={kind === 'dot' ? 'rgba(255,138,42,.24)' : 'rgba(255,138,42,.2)'}
        filter="url(#v6-floor-blur)"
        transform={`rotate(${ISO_ANGLE} ${at.x} ${at.y + 74})`}
      />
      {ghosts.map((g, i) =>
        kind === 'clip' ? (
          <foreignObject
            key={`ghost${i}`}
            x={g.p.x - 38}
            y={g.p.y - 68}
            width={76}
            height={150}
            opacity={g.k * 0.24}
            style={{overflow: 'visible'}}
          >
            <ClipArt width={76} radius={12} border="1px solid rgba(255,255,255,.12)" />
          </foreignObject>
        ) : kind === 'dot' ? (
          <circle key={`ghost${i}`} cx={g.p.x} cy={g.p.y} r={3 + g.k * 7} fill={colour} opacity={g.k * 0.28} />
        ) : null,
      )}
      {kind === 'clip' ? (
        <foreignObject x={at.x - 50} y={at.y - 104} width={100} height={215} style={{overflow: 'visible'}}>
          <div style={{transform: `rotate(${-4 + angle * 0.1}deg)`, width: 100}}>
            <ClipArt
              width={100}
              radius={16}
              border="1.5px solid rgba(255,255,255,.72)"
              style={{boxShadow: '0 0 0 1px rgba(255,138,42,.4), 0 20px 44px rgba(0,0,0,.8), 0 0 52px rgba(255,138,42,.45)'}}
            />
            {subLabel ? (
              <div
                style={{
                  marginTop: 5,
                  fontFamily,
                  fontSize: 36,
                  fontWeight: 600,
                  letterSpacing: '.04em',
                  textAlign: 'center',
                  color: '#FFC08A',
                  textShadow: '0 2px 8px rgba(0,0,0,.9)',
                  whiteSpace: 'nowrap',
                }}
              >
                {subLabel}
              </div>
            ) : null}
          </div>
        </foreignObject>
      ) : kind === 'job' ? (
        <foreignObject x={at.x - 100} y={at.y - 54} width={200} height={108} style={{overflow: 'visible'}}>
          <div
            style={{
              width: 200,
              height: 100,
              borderRadius: 24,
              background: '#1C1E22',
              border: '1.5px solid rgba(255,158,84,.6)',
              boxShadow: '0 16px 34px rgba(0,0,0,.6), 0 0 30px rgba(255,138,42,.22)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 14,
              fontFamily,
              fontSize: 40,
              fontWeight: 500,
              letterSpacing: '.02em',
              color: C.text,
            }}
          >
            <div style={{width: 13, height: 13, borderRadius: 7, background: C.hot}} />
            job
          </div>
        </foreignObject>
      ) : (
        <g>
          <circle cx={at.x} cy={at.y} r={26} fill={colour} opacity={0.14} />
          <circle cx={at.x} cy={at.y} r={15} fill={colour} opacity={0.32} />
          <circle cx={at.x} cy={at.y} r={8} fill={colour} />
        </g>
      )}
    </g>
  );
};

export const Caption: React.FC<{text: string; from: number; to: number; hot?: string; y?: number}> = ({
  text,
  from,
  to,
  hot,
  y = 952,
}) => {
  const f = useCurrentFrame();
  const inP = ease(f, from, from + 10);
  const opacity = inP * (1 - ease(f, to - 10, to));
  if (opacity <= 0) return null;
  const words = text.split(' ');
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: y,
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
        opacity,
      }}
    >
      <div
        style={{
          fontFamily,
          fontSize: 44,
          fontWeight: 600,
          letterSpacing: '.005em',
          color: C.text,
          background: 'rgba(8,9,11,.8)',
          border: `1px solid ${C.rule}`,
          borderRadius: 999,
          padding: '15px 38px 17px',
          transform: `translateY(${(1 - inP) * 8}px)`,
          boxShadow: '0 18px 50px rgba(0,0,0,.5)',
        }}
      >
        {words.map((word, i) => (
          <React.Fragment key={`${word}-${i}`}>
            {i > 0 ? ' ' : ''}
            <span style={{color: hot && word === hot ? C.hot : C.text}}>{word}</span>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

export const Counter: React.FC<{
  x: number;
  y: number;
  value: number;
  label?: string;
  opacity?: number;
}> = ({x, y, value, label = 'running', opacity = 1}) => {
  if (opacity <= 0.001) return null;
  const live = value > 0.5;
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        transform: 'translate(-50%, -50%)',
        fontFamily,
        fontSize: 56,
        fontWeight: 600,
        letterSpacing: '-.02em',
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
        color: live ? '#FFC08A' : '#8E9AAB',
        opacity,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        textShadow: live ? '0 0 34px rgba(255,138,42,.4)' : 'none',
      }}
    >
      <div
        style={{
          width: 12,
          height: 12,
          borderRadius: 6,
          background: live ? C.hot : '#5C6775',
          boxShadow: live ? '0 0 16px rgba(255,138,42,.8)' : 'none',
        }}
      />
      {Math.round(value)} {label}
    </div>
  );
};
