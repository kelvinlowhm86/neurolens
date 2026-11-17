import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {arrowD, bez, bezAngle, ClipArt, flight, pathD, Pt, trail} from './shared';

export const DURATION = 60;

const POS = {
  browser: {x: 540, y: 384},
  storage: {x: 1390, y: 364},
  website: {x: 640, y: 790},
  queue: {x: 1320, y: 778},
};

const START: Pt = {x: POS.browser.x + 70, y: POS.browser.y};
const CTRL: Pt = {x: 965, y: 138};
const END: Pt = {x: POS.storage.x - 70, y: POS.storage.y};
const WEB_R: Pt = {x: POS.website.x + 84, y: POS.website.y};
const QUEUE_L: Pt = {x: POS.queue.x - 84, y: POS.queue.y};
const WEB_CTRL: Pt = {x: 965, y: 862};

const LINK_A: Pt = {x: POS.browser.x - 26, y: POS.browser.y + 50};
const LINK_C: Pt = {x: 408, y: 588};
const LINK_B: Pt = {x: POS.website.x - 26, y: POS.website.y - 66};

const ISO = 'rotateX(58deg) rotateZ(-45deg)';

const Globe: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <circle cx="32" cy="32" r="22" stroke="currentColor" strokeWidth="2" />
    <ellipse cx="32" cy="32" rx="10" ry="22" stroke="currentColor" strokeWidth="2" />
    <path d="M10 32H54" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <path d="M14 21.5C19 24.4 24.9 26 32 26C39.1 26 45 24.4 50 21.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity=".55" />
    <path d="M14 42.5C19 39.6 24.9 38 32 38C39.1 38 45 39.6 50 42.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity=".55" />
  </svg>
);

const Window: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <rect x="10" y="14" width="44" height="36" rx="7" stroke="currentColor" strokeWidth="2" />
    <path d="M10 24H54" stroke="currentColor" strokeWidth="2" />
    <circle cx="17.5" cy="19" r="1.7" fill="currentColor" />
    <circle cx="23.5" cy="19" r="1.7" fill="currentColor" opacity=".65" />
    <path d="M18 33H34M18 39H29" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" opacity=".6" />
    <rect x="38" y="31" width="9" height="11" rx="2" stroke="currentColor" strokeWidth="1.4" opacity=".6" />
  </svg>
);

const Cylinder: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <ellipse cx="32" cy="18" rx="20" ry="6.5" stroke="currentColor" strokeWidth="2" />
    <path d="M12 18V46C12 49.6 20.9 52.5 32 52.5C43.1 52.5 52 49.6 52 46V18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <path d="M12 32C12 35.6 20.9 38.5 32 38.5C43.1 38.5 52 35.6 52 32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".6" />
    <path d="M20 46.5C23 48 27 49 32 49C37 49 41 48 44 46.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" opacity=".45" />
  </svg>
);

const Stack: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <rect x="13" y="15" width="38" height="9" rx="4.5" stroke="currentColor" strokeWidth="2" />
    <rect x="17" y="28" width="30" height="9" rx="4.5" stroke="currentColor" strokeWidth="2" opacity=".8" />
    <rect x="21" y="41" width="22" height="9" rx="4.5" stroke="currentColor" strokeWidth="2" opacity=".6" />
  </svg>
);

const Node: React.FC<{
  x: number;
  y: number;
  label: string;
  service: string;
  active?: boolean;
  icon: React.ReactNode;
}> = ({x, y, label, service, active, icon}) => <div style={{
  position: 'absolute',
  left: x - 170,
  top: y - 68,
  width: 340,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  fontFamily,
}}>
  <div style={{position: 'relative', width: 136, height: 146}}>
    <div style={{
      position: 'absolute',
      left: 8,
      top: 120,
      width: 120,
      height: 22,
      borderRadius: '50%',
      background: active
        ? 'radial-gradient(ellipse at center, rgba(255,138,42,.30), transparent 70%)'
        : 'radial-gradient(ellipse at center, rgba(120,138,160,.16), transparent 70%)',
      filter: 'blur(6px)',
    }} />
    <div style={{
      position: 'absolute',
      left: 18,
      top: 128,
      width: 100,
      height: 12,
      borderRadius: '50%',
      border: `1px solid ${active ? 'rgba(255,158,84,.22)' : 'rgba(150,168,190,.16)'}`,
    }} />
    <div style={{
      position: 'absolute',
      left: 0,
      top: 0,
      width: 136,
      height: 136,
      borderRadius: 68,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: active ? '#FFC08A' : '#8A9099',
      background: active
        ? 'radial-gradient(circle at 50% 42%, rgba(255,138,42,.22) 0%, rgba(255,138,42,.05) 54%, transparent 72%)'
        : 'radial-gradient(circle at 50% 42%, rgba(120,138,160,.13) 0%, rgba(120,138,160,.035) 54%, transparent 72%)',
      border: `1px solid ${active ? 'rgba(255,138,42,.34)' : 'rgba(255,255,255,.10)'}`,
      boxShadow: active
        ? '0 0 46px rgba(255,138,42,.18), inset 0 0 26px rgba(255,138,42,.08)'
        : '0 0 30px rgba(120,138,160,.07), inset 0 0 22px rgba(120,138,160,.04)',
    }}>{icon}</div>
  </div>
  <div style={{
    marginTop: 6,
    fontSize: 46,
    fontWeight: 600,
    letterSpacing: '-.02em',
    lineHeight: 1,
    color: active ? C.text : '#BFC5CD',
  }}>{label}</div>
  <div style={{
    marginTop: 11,
    fontSize: 24,
    letterSpacing: '.07em',
    color: active ? '#C39C7F' : '#7F8791',
  }}>{service}</div>
</div>;

const pathToStorage = pathD(START, CTRL, END);
const pathToQueue = pathD(WEB_R, WEB_CTRL, QUEUE_L);
const pathToWebsite = pathD(LINK_A, LINK_C, LINK_B);

export const StyleE_Mix: React.FC = () => {
  const f = useCurrentFrame();
  const t = flight(f);
  const at = bez(START, CTRL, END, t);
  const angle = bezAngle(START, CTRL, END, t);
  const ghosts = trail(START, CTRL, END, t, 5, 0.24);
  const sparks = Array.from({length: 16}, (_, i) => {
    const tt = Math.max(0, t - (0.26 * (i + 1)) / 16);
    return {p: bez(START, CTRL, END, tt), k: 1 - (i + 1) / 17};
  });

  return <AbsoluteFill style={{
    background:
      'radial-gradient(1300px 900px at 50% 40%, #0B0E13 0%, #07090C 56%, #040507 100%)',
  }}>
    <div style={{
      position: 'absolute',
      left: '50%',
      top: '60%',
      width: 2800,
      height: 2800,
      transform: `translate(-50%, -50%) ${ISO}`,
      backgroundImage:
        'linear-gradient(rgba(139,166,194,.12) 1px, transparent 1px), linear-gradient(90deg, rgba(139,166,194,.12) 1px, transparent 1px)',
      backgroundSize: '84px 84px',
      border: '1px solid rgba(139,166,194,.09)',
      borderRadius: 80,
      maskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
      WebkitMaskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
    }} />
    <div style={{
      position: 'absolute',
      left: '50%',
      top: '54%',
      width: 1500,
      height: 900,
      transform: 'translate(-50%, -50%)',
      background: 'radial-gradient(ellipse at center, rgba(66,88,118,.20), transparent 66%)',
      filter: 'blur(20px)',
    }} />
    <div style={{
      position: 'absolute',
      left: '50%',
      top: '58%',
      width: 1360,
      height: 640,
      transform: 'translate(-50%, -50%) rotate(-3deg)',
      background: 'radial-gradient(ellipse at center, rgba(255,138,42,.13), transparent 66%)',
      filter: 'blur(34px)',
    }} />

    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
      <defs>
        <linearGradient id="e-hot" gradientUnits="userSpaceOnUse" x1={START.x} y1={START.y} x2={END.x} y2={END.y}>
          <stop offset="0" stopColor="#FF8A2A" stopOpacity=".2" />
          <stop offset=".5" stopColor="#FFBE88" stopOpacity=".95" />
          <stop offset="1" stopColor="#FF8A2A" stopOpacity=".45" />
        </linearGradient>
        <linearGradient id="e-cool" gradientUnits="userSpaceOnUse" x1={WEB_R.x} y1={WEB_R.y} x2={QUEUE_L.x} y2={QUEUE_L.y}>
          <stop offset="0" stopColor="#425165" stopOpacity=".5" />
          <stop offset="1" stopColor="#7589A3" stopOpacity=".8" />
        </linearGradient>
        <filter id="e-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="8" />
        </filter>
        <filter id="e-soft" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      <path d={pathToStorage} fill="none" stroke="rgba(255,138,42,.4)" strokeWidth={13} strokeLinecap="round" filter="url(#e-glow)" />
      <path d={pathToStorage} fill="none" stroke="url(#e-hot)" strokeWidth={2.6} strokeLinecap="round" />
      <path d={arrowD(END, bezAngle(START, CTRL, END, 1), 13)} fill="none" stroke="rgba(255,186,136,.9)" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
      <g opacity=".9">
        {sparks.map((s, i) => <circle key={i} cx={s.p.x} cy={s.p.y} r={1.3 + s.k * 3.2} fill="#FFB57C" opacity={s.k * 0.5} filter="url(#e-soft)" />)}
      </g>

      <path d={pathToWebsite} fill="none" stroke="rgba(150,166,188,.34)" strokeWidth={1.5} strokeLinecap="round" />
      <circle cx={LINK_A.x} cy={LINK_A.y} r={2.4} fill="rgba(160,176,198,.5)" />
      <circle cx={LINK_B.x} cy={LINK_B.y} r={2.4} fill="rgba(160,176,198,.5)" />

      <path d={pathToQueue} fill="none" stroke="rgba(117,137,163,.16)" strokeWidth={9} strokeLinecap="round" filter="url(#e-glow)" />
      <path d={pathToQueue} fill="none" stroke="url(#e-cool)" strokeWidth={1.9} strokeLinecap="round" />
      <path d={arrowD(QUEUE_L, bezAngle(WEB_R, WEB_CTRL, QUEUE_L, 1), 11)} fill="none" stroke="rgba(150,170,196,.7)" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" />
    </svg>

    <Node x={POS.browser.x} y={POS.browser.y} label="Browser" service="the file" active icon={<Globe size={64} />} />
    <Node x={POS.storage.x} y={POS.storage.y} label="Storage" service="S3" active icon={<Cylinder size={64} />} />
    <Node x={POS.website.x} y={POS.website.y} label="Website" service="the app" icon={<Window size={64} />} />
    <Node x={POS.queue.x} y={POS.queue.y} label="Queue" service="SQS" icon={<Stack size={64} />} />

    <div style={{
      position: 'absolute',
      left: at.x - 200,
      top: at.y + 118,
      width: 400,
      height: 190,
      borderRadius: '50%',
      background: 'radial-gradient(ellipse at center, rgba(255,138,42,.24), transparent 68%)',
      filter: 'blur(22px)',
      transform: 'rotate(-6deg)',
    }} />

    <div style={{
      position: 'absolute',
      left: at.x - 160,
      top: at.y - 160,
      width: 320,
      height: 320,
      borderRadius: '50%',
      background: 'radial-gradient(circle, rgba(255,138,42,.24), transparent 66%)',
      filter: 'blur(16px)',
    }} />

    {ghosts.map((g, i) => <ClipArt
      key={i}
      width={80 * (0.72 + 0.28 * g.k)}
      radius={14}
      border="1px solid rgba(255,255,255,.12)"
      style={{
        position: 'absolute',
        left: g.p.x - (80 * (0.72 + 0.28 * g.k)) / 2,
        top: g.p.y - ((80 * (0.72 + 0.28 * g.k)) * 8) / 9,
        opacity: g.k * 0.22,
        transform: `rotate(${-5 + angle * 0.13}deg)`,
        filter: 'blur(1.5px)',
      }}
    />)}

    <div style={{
      position: 'absolute',
      left: at.x - 40,
      top: at.y - 71,
      transform: `rotate(${-5 + angle * 0.13}deg)`,
    }}>
      <ClipArt
        width={80}
        radius={15}
        border="1.5px solid rgba(255,255,255,.72)"
        style={{
          boxShadow: '0 0 0 1px rgba(255,138,42,.4), 0 26px 56px rgba(0,0,0,.8), 0 0 60px rgba(255,138,42,.5)',
        }}
      />
    </div>
  </AbsoluteFill>;
};
