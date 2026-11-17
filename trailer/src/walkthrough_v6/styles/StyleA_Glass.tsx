import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {bez, bezAngle, ClipArt, flight, pathD, Pt, trail} from './shared';

export const DURATION = 60;

const W = 280;
const H = 172;

const POS = {
  browser: {x: 200, y: 340},
  storage: {x: 1390, y: 288},
  website: {x: 292, y: 700},
  queue: {x: 1244, y: 724},
};

const START: Pt = {x: POS.browser.x + W, y: POS.browser.y + H / 2};
const CTRL: Pt = {x: 940, y: 196};
const END: Pt = {x: POS.storage.x, y: POS.storage.y + H / 2};
const WEB_R: Pt = {x: POS.website.x + W, y: POS.website.y + H / 2};
const QUEUE_L: Pt = {x: POS.queue.x, y: POS.queue.y + H / 2};
const WEB_CTRL: Pt = {x: 918, y: 862};

const serviceColour = (active?: boolean) => (active ? '#DDB395' : '#8E959E');

const Panel: React.FC<{
  x: number;
  y: number;
  label: string;
  service: string;
  depth: number;
  active?: boolean;
  recede?: boolean;
}> = ({x, y, label, service, depth, active, recede}) => (
  <div style={{
    position: 'absolute',
    left: x,
    top: y,
    width: W,
    height: H,
    borderRadius: 32,
    transform: `translateZ(${depth}px)`,
    background: active
      ? 'linear-gradient(155deg, rgba(255,255,255,.15), rgba(255,255,255,.055) 52%, rgba(255,138,42,.09))'
      : 'linear-gradient(155deg, rgba(255,255,255,.09), rgba(255,255,255,.035) 55%, rgba(255,255,255,.055))',
    border: `1px solid ${active ? 'rgba(255,172,104,.58)' : 'rgba(255,255,255,.14)'}`,
    boxShadow: active
      ? '0 40px 80px -30px rgba(0,0,0,.9), 0 0 64px rgba(255,138,42,.20), inset 0 1px 0 rgba(255,255,255,.36), inset 0 -30px 50px -40px rgba(255,138,42,.55)'
      : '0 36px 70px -30px rgba(0,0,0,.85), inset 0 1px 0 rgba(255,255,255,.22)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily,
    opacity: recede ? 0.78 : 1,
    overflow: 'hidden',
  }}>
    <div style={{
      position: 'absolute',
      left: 0,
      right: 0,
      top: 0,
      height: '52%',
      background: 'linear-gradient(180deg, rgba(255,255,255,.11), transparent)',
      pointerEvents: 'none',
    }} />
    <div style={{fontSize: 46, fontWeight: 600, letterSpacing: '-.02em', color: C.text, lineHeight: 1}}>{label}</div>
    <div style={{
      fontSize: 25,
      fontWeight: 400,
      letterSpacing: '.06em',
      color: serviceColour(active),
      marginTop: 12,
    }}>{service}</div>
  </div>
);

const pathToStorage = pathD(START, CTRL, END);
const pathToQueue = pathD(WEB_R, WEB_CTRL, QUEUE_L);

export const StyleA_Glass: React.FC = () => {
  const f = useCurrentFrame();
  const t = flight(f);
  const at = bez(START, CTRL, END, t);
  const angle = bezAngle(START, CTRL, END, t);
  const ghosts = trail(START, CTRL, END, t, 5, 0.24);

  return <AbsoluteFill style={{background: '#07080A'}}>
    <AbsoluteFill style={{
      background:
        'radial-gradient(1100px 760px at 70% 26%, rgba(255,138,42,.14), transparent 62%), radial-gradient(1000px 820px at 22% 74%, rgba(96,116,148,.17), transparent 66%), linear-gradient(180deg,#0A0B0E 0%,#07080A 55%,#050608 100%)',
    }} />

    <AbsoluteFill style={{perspective: 2000, perspectiveOrigin: '50% 46%'}}>
      <AbsoluteFill style={{
        transformStyle: 'preserve-3d',
        transform: 'rotateX(8deg) rotateY(-6deg) rotateZ(1deg)',
        transformOrigin: '50% 50%',
      }}>
        <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
          <defs>
            <linearGradient id="a-hot" gradientUnits="userSpaceOnUse" x1={START.x} y1={START.y} x2={END.x} y2={END.y}>
              <stop offset="0" stopColor="#FF8A2A" stopOpacity=".22" />
              <stop offset=".48" stopColor="#FFB57C" stopOpacity=".95" />
              <stop offset="1" stopColor="#FF8A2A" stopOpacity=".42" />
            </linearGradient>
            <linearGradient id="a-cool" gradientUnits="userSpaceOnUse" x1={WEB_R.x} y1={WEB_R.y} x2={QUEUE_L.x} y2={QUEUE_L.y}>
              <stop offset="0" stopColor="#39414C" stopOpacity=".55" />
              <stop offset="1" stopColor="#66788F" stopOpacity=".8" />
            </linearGradient>
            <filter id="a-glow" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="9" />
            </filter>
          </defs>
          <path d={pathToStorage} fill="none" stroke="rgba(255,138,42,.42)" strokeWidth={15} strokeLinecap="round" filter="url(#a-glow)" />
          <path d={pathToStorage} fill="none" stroke="url(#a-hot)" strokeWidth={3} strokeLinecap="round" />
          <path d={pathToQueue} fill="none" stroke="url(#a-cool)" strokeWidth={2.2} strokeLinecap="round" />
          <circle cx={QUEUE_L.x} cy={QUEUE_L.y} r={4} fill="rgba(140,160,186,.55)" />
        </svg>

        <Panel x={POS.browser.x} y={POS.browser.y} label="Browser" service="the file" depth={24} active />
        <Panel x={POS.storage.x} y={POS.storage.y} label="Storage" service="S3" depth={62} active />
        <Panel x={POS.website.x} y={POS.website.y} label="Website" service="the app" depth={-26} recede />
        <Panel x={POS.queue.x} y={POS.queue.y} label="Queue" service="SQS" depth={4} />

        <div style={{
          position: 'absolute',
          left: at.x - 170,
          top: at.y - 170,
          width: 340,
          height: 340,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(255,138,42,.30), transparent 66%)',
          filter: 'blur(14px)',
          transform: 'translateZ(74px)',
        }} />

        {ghosts.map((g, i) => <ClipArt
          key={i}
          width={86 * (0.72 + 0.28 * g.k)}
          radius={15}
          border="1px solid rgba(255,255,255,.14)"
          style={{
            position: 'absolute',
            left: g.p.x - (86 * (0.72 + 0.28 * g.k)) / 2,
            top: g.p.y - ((86 * (0.72 + 0.28 * g.k)) * 16) / 18,
            opacity: g.k * 0.26,
            transform: `translateZ(74px) rotate(${-6 + angle * 0.15}deg)`,
            filter: 'blur(1.5px)',
          }}
        />)}

        <div style={{
          position: 'absolute',
          left: at.x - 43,
          top: at.y - 76,
          transform: `translateZ(84px) rotate(${-6 + angle * 0.15}deg)`,
        }}>
          <ClipArt
            width={86}
            radius={16}
            border="1px solid rgba(255,255,255,.65)"
            style={{
              boxShadow: '0 0 0 1px rgba(255,138,42,.35), 0 28px 62px rgba(0,0,0,.8), 0 0 60px rgba(255,138,42,.5)',
            }}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  </AbsoluteFill>;
};
