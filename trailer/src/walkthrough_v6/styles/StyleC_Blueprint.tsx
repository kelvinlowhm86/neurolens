import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {fontFamily} from '../../edit/ui';
import {arrowD, bez, bezAngle, ClipArt, flight, MONO, pathD, Pt} from './shared';

export const DURATION = 60;

const W = 300;
const H = 176;

const POS = {
  browser: {x: 260, y: 300},
  storage: {x: 1360, y: 270},
  website: {x: 340, y: 670},
  queue: {x: 1280, y: 650},
};

const START: Pt = {x: POS.browser.x + W, y: POS.browser.y + H / 2};
const CTRL: Pt = {x: 965, y: 186};
const END: Pt = {x: POS.storage.x, y: POS.storage.y + H / 2};
const WEB_R: Pt = {x: POS.website.x + W, y: POS.website.y + H / 2};
const QUEUE_L: Pt = {x: POS.queue.x, y: POS.queue.y + H / 2};
const WEB_CTRL: Pt = {x: 960, y: 718};

const STEEL = 'rgba(126,158,186,.42)';
const STEEL_DIM = 'rgba(126,158,186,.18)';

const Tick: React.FC<{x: number; y: number; flipX?: boolean; flipY?: boolean; colour: string}> = ({x, y, flipX, flipY, colour}) => <div style={{
  position: 'absolute',
  left: x,
  top: y,
  width: 13,
  height: 13,
  borderLeft: flipX ? undefined : `1px solid ${colour}`,
  borderRight: flipX ? `1px solid ${colour}` : undefined,
  borderTop: flipY ? undefined : `1px solid ${colour}`,
  borderBottom: flipY ? `1px solid ${colour}` : undefined,
  boxSizing: 'border-box',
}} />;

const Frame: React.FC<{
  x: number;
  y: number;
  label: string;
  service: string;
  active?: boolean;
}> = ({x, y, label, service, active}) => {
  const colour = active ? 'rgba(255,138,42,.62)' : STEEL;
  return <div style={{
    position: 'absolute',
    left: x,
    top: y,
    width: W,
    height: H,
    boxSizing: 'border-box',
    border: `1px solid ${active ? 'rgba(255,138,42,.55)' : 'rgba(126,158,186,.28)'}`,
    background: 'rgba(8,13,18,.42)',
    fontFamily,
  }}>
    <div style={{position: 'absolute', inset: 5, border: `1px solid ${active ? 'rgba(255,138,42,.14)' : STEEL_DIM}`}} />
    <Tick x={-1} y={-1} colour={colour} />
    <Tick x={W - 12} y={-1} flipX colour={colour} />
    <Tick x={-1} y={H - 12} flipY colour={colour} />
    <Tick x={W - 12} y={H - 12} flipX flipY colour={colour} />
    <div style={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <div style={{
        fontSize: 44,
        fontWeight: 500,
        letterSpacing: '.07em',
        lineHeight: 1,
        color: active ? '#F4F1EC' : '#D3DAE2',
        textTransform: 'uppercase',
      }}>{label}</div>
      <div style={{
        marginTop: 15,
        fontFamily: MONO,
        fontSize: 21,
        letterSpacing: '.18em',
        textTransform: 'uppercase',
        color: active ? 'rgba(255,178,120,.8)' : 'rgba(126,158,186,.75)',
      }}>{service}</div>
    </div>
  </div>;
};

const pathToStorage = pathD(START, CTRL, END);
const pathToQueue = pathD(WEB_R, WEB_CTRL, QUEUE_L);

const Cross: React.FC<{p: Pt; colour: string}> = ({p, colour}) => <g stroke={colour} strokeWidth={1}>
  <path d={`M ${p.x - 7} ${p.y} H ${p.x + 7}`} />
  <path d={`M ${p.x} ${p.y - 7} V ${p.y + 7}`} />
</g>;

export const StyleC_Blueprint: React.FC = () => {
  const f = useCurrentFrame();
  const t = flight(f);
  const at = bez(START, CTRL, END, t);
  const angle = bezAngle(START, CTRL, END, t);
  const dots = Array.from({length: 9}, (_, i) => {
    const tt = Math.max(0, t - (0.22 * (i + 1)) / 9);
    return {p: bez(START, CTRL, END, tt), k: 1 - (i + 1) / 10};
  });

  return <AbsoluteFill style={{background: '#05080B'}}>
    <AbsoluteFill style={{
      backgroundImage: 'radial-gradient(circle, rgba(116,150,180,.20) 1px, transparent 1.7px)',
      backgroundSize: '30px 30px',
      maskImage: 'radial-gradient(ellipse 70% 68% at 50% 48%, #000 0%, rgba(0,0,0,.72) 58%, transparent 88%)',
      WebkitMaskImage: 'radial-gradient(ellipse 70% 68% at 50% 48%, #000 0%, rgba(0,0,0,.72) 58%, transparent 88%)',
    }} />
    <AbsoluteFill style={{
      background: 'radial-gradient(1100px 760px at 68% 30%, rgba(255,138,42,.055), transparent 62%), radial-gradient(900px 700px at 24% 72%, rgba(92,116,148,.10), transparent 66%)',
    }} />

    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
      <defs>
        <linearGradient id="c-hot" gradientUnits="userSpaceOnUse" x1={START.x} y1={START.y} x2={END.x} y2={END.y}>
          <stop offset="0" stopColor="#FF8A2A" stopOpacity=".35" />
          <stop offset=".52" stopColor="#FFC79A" stopOpacity=".95" />
          <stop offset="1" stopColor="#FF8A2A" stopOpacity=".5" />
        </linearGradient>
        <linearGradient id="c-cool" gradientUnits="userSpaceOnUse" x1={WEB_R.x} y1={WEB_R.y} x2={QUEUE_L.x} y2={QUEUE_L.y}>
          <stop offset="0" stopColor="#54708C" stopOpacity=".45" />
          <stop offset="1" stopColor="#7D95AE" stopOpacity=".7" />
        </linearGradient>
      </defs>

      <path d={pathToStorage} fill="none" stroke="rgba(255,138,42,.20)" strokeWidth={5} strokeLinecap="round" />
      <path d={pathToStorage} fill="none" stroke="url(#c-hot)" strokeWidth={1.4} strokeLinecap="round" />
      <path d={arrowD(END, bezAngle(START, CTRL, END, 1), 11)} fill="none" stroke="rgba(255,190,140,.85)" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" />
      <Cross p={START} colour="rgba(255,160,100,.6)" />
      <Cross p={END} colour="rgba(255,160,100,.6)" />

      <path d={pathToQueue} fill="none" stroke="url(#c-cool)" strokeWidth={1.1} strokeLinecap="round" />
      <path d={arrowD(QUEUE_L, bezAngle(WEB_R, WEB_CTRL, QUEUE_L, 1), 9)} fill="none" stroke="rgba(150,172,198,.7)" strokeWidth={1.1} strokeLinecap="round" strokeLinejoin="round" />

      {dots.map((d, i) => <rect
        key={i}
        x={d.p.x - 2.5}
        y={d.p.y - 2.5}
        width={5}
        height={5}
        fill="#FFAE72"
        opacity={d.k * 0.55}
        transform={`rotate(45 ${d.p.x} ${d.p.y})`}
      />)}
    </svg>

    <Frame x={POS.browser.x} y={POS.browser.y} label="Browser" service="client" active />
    <Frame x={POS.storage.x} y={POS.storage.y} label="Storage" service="S3" active />
    <Frame x={POS.website.x} y={POS.website.y} label="Website" service="app" />
    <Frame x={POS.queue.x} y={POS.queue.y} label="Queue" service="SQS" />

    <div style={{
      position: 'absolute',
      left: at.x - 90,
      top: at.y - 90,
      width: 180,
      height: 180,
      borderRadius: '50%',
      background: 'radial-gradient(circle, rgba(255,138,42,.18), transparent 66%)',
      filter: 'blur(14px)',
    }} />

    <div style={{
      position: 'absolute',
      left: at.x - 36,
      top: at.y - 64,
      transform: `rotate(${-4 + angle * 0.12}deg)`,
    }}>
      <ClipArt
        width={72}
        radius={3}
        border="1px solid rgba(255,255,255,.78)"
        style={{
          boxShadow: '0 0 0 1px rgba(255,138,42,.35), 0 0 34px rgba(255,138,42,.35), 0 18px 38px rgba(0,0,0,.7)',
        }}
      />
    </div>
  </AbsoluteFill>;
};
