import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {arrowD, bez, bezAngle, ClipArt, flight, pathD, Pt, trail} from './shared';

export const DURATION = 60;

const POS = {
  browser: {x: 470, y: 380},
  storage: {x: 1450, y: 340},
  website: {x: 560, y: 735},
  queue: {x: 1350, y: 700},
};

const START: Pt = {x: POS.browser.x + 74, y: POS.browser.y};
const CTRL: Pt = {x: 960, y: 148};
const END: Pt = {x: POS.storage.x - 74, y: POS.storage.y};
const WEB_R: Pt = {x: POS.website.x + 84, y: POS.website.y};
const QUEUE_L: Pt = {x: POS.queue.x - 84, y: POS.queue.y};
const WEB_CTRL: Pt = {x: 955, y: 798};

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
  left: x - 150,
  top: y - 64,
  width: 300,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  fontFamily,
}}>
  <div style={{
    width: 128,
    height: 128,
    borderRadius: 64,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: active ? '#FFC08A' : '#8A9099',
    background: active
      ? 'radial-gradient(circle, rgba(255,138,42,.20) 0%, rgba(255,138,42,.05) 52%, transparent 72%)'
      : 'radial-gradient(circle, rgba(120,138,160,.12) 0%, rgba(120,138,160,.03) 52%, transparent 72%)',
    boxShadow: active ? '0 0 44px rgba(255,138,42,.16)' : 'none',
  }}>{icon}</div>
  <div style={{
    marginTop: 16,
    fontSize: 46,
    fontWeight: 600,
    letterSpacing: '-.02em',
    lineHeight: 1,
    color: active ? C.text : '#BFC5CD',
  }}>{label}</div>
  <div style={{
    marginTop: 10,
    fontSize: 24,
    letterSpacing: '.07em',
    color: active ? '#C39C7F' : '#7F8791',
  }}>{service}</div>
</div>;

const pathToStorage = pathD(START, CTRL, END);
const pathToQueue = pathD(WEB_R, WEB_CTRL, QUEUE_L);

export const StyleB_Icons: React.FC = () => {
  const f = useCurrentFrame();
  const t = flight(f);
  const at = bez(START, CTRL, END, t);
  const angle = bezAngle(START, CTRL, END, t);
  const ghosts = trail(START, CTRL, END, t, 6, 0.26);
  const sparks = Array.from({length: 18}, (_, i) => {
    const tt = Math.max(0, t - (0.26 * (i + 1)) / 18);
    return {p: bez(START, CTRL, END, tt), k: 1 - (i + 1) / 19};
  });

  return <AbsoluteFill style={{
    background:
      'radial-gradient(1200px 820px at 50% 38%, #0C0E12 0%, #08090C 58%, #050608 100%)',
  }}>
    <AbsoluteFill style={{
      background:
        'radial-gradient(820px 560px at 70% 30%, rgba(255,138,42,.10), transparent 62%), radial-gradient(760px 560px at 26% 70%, rgba(92,112,144,.13), transparent 66%)',
    }} />

    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
      <defs>
        <linearGradient id="b-hot" gradientUnits="userSpaceOnUse" x1={START.x} y1={START.y} x2={END.x} y2={END.y}>
          <stop offset="0" stopColor="#FF8A2A" stopOpacity=".18" />
          <stop offset=".5" stopColor="#FFC08A" stopOpacity=".95" />
          <stop offset="1" stopColor="#FF8A2A" stopOpacity=".4" />
        </linearGradient>
        <linearGradient id="b-cool" gradientUnits="userSpaceOnUse" x1={WEB_R.x} y1={WEB_R.y} x2={QUEUE_L.x} y2={QUEUE_L.y}>
          <stop offset="0" stopColor="#3C4654" stopOpacity=".5" />
          <stop offset="1" stopColor="#7589A3" stopOpacity=".75" />
        </linearGradient>
        <filter id="b-glow" x="-45%" y="-45%" width="190%" height="190%">
          <feGaussianBlur stdDeviation="7" />
        </filter>
        <filter id="b-soft" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      <path d={pathToStorage} fill="none" stroke="rgba(255,138,42,.45)" strokeWidth={12} strokeLinecap="round" filter="url(#b-glow)" />
      <path d={pathToStorage} fill="none" stroke="url(#b-hot)" strokeWidth={2.4} strokeLinecap="round" />
      <g opacity=".9">
        {sparks.map((s, i) => <circle key={i} cx={s.p.x} cy={s.p.y} r={1.4 + s.k * 3.4} fill="#FFB57C" opacity={s.k * 0.55} filter="url(#b-soft)" />)}
      </g>

      <path d={pathToQueue} fill="none" stroke="rgba(117,137,163,.18)" strokeWidth={8} strokeLinecap="round" filter="url(#b-glow)" />
      <path d={pathToQueue} fill="none" stroke="url(#b-cool)" strokeWidth={1.8} strokeLinecap="round" />
      <path d={arrowD(QUEUE_L, bezAngle(WEB_R, WEB_CTRL, QUEUE_L, 1), 11)} fill="none" stroke="rgba(150,170,196,.7)" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
      <path d={arrowD(END, bezAngle(START, CTRL, END, 1), 12)} fill="none" stroke="rgba(255,176,116,.85)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>

    <Node x={POS.browser.x} y={POS.browser.y} label="Browser" service="the file" active icon={<Globe size={64} />} />
    <Node x={POS.storage.x} y={POS.storage.y} label="Storage" service="S3" active icon={<Cylinder size={64} />} />
    <Node x={POS.website.x} y={POS.website.y} label="Website" service="the app" icon={<Window size={64} />} />
    <Node x={POS.queue.x} y={POS.queue.y} label="Queue" service="SQS" icon={<Stack size={64} />} />

    <div style={{
      position: 'absolute',
      left: at.x - 175,
      top: at.y - 175,
      width: 350,
      height: 350,
      borderRadius: '50%',
      background: 'radial-gradient(circle, rgba(255,138,42,.26), transparent 66%)',
      filter: 'blur(18px)',
    }} />

    {ghosts.map((g, i) => <ClipArt
      key={i}
      width={82 * (0.7 + 0.3 * g.k)}
      radius={14}
      border="1px solid rgba(255,255,255,.12)"
      style={{
        position: 'absolute',
        left: g.p.x - (82 * (0.7 + 0.3 * g.k)) / 2,
        top: g.p.y - ((82 * (0.7 + 0.3 * g.k)) * 8) / 9,
        opacity: g.k * 0.24,
        transform: `rotate(${-5 + angle * 0.14}deg)`,
        filter: 'blur(1.5px)',
      }}
    />)}

    <div style={{
      position: 'absolute',
      left: at.x - 41,
      top: at.y - 73,
      transform: `rotate(${-5 + angle * 0.14}deg)`,
    }}>
      <ClipArt
        width={82}
        radius={15}
        border="1.5px solid rgba(255,255,255,.75)"
        style={{
          boxShadow: '0 0 0 1px rgba(255,138,42,.4), 0 24px 54px rgba(0,0,0,.78), 0 0 64px rgba(255,138,42,.55)',
        }}
      />
    </div>
  </AbsoluteFill>;
};
