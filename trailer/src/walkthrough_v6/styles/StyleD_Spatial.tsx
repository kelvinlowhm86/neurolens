import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {arrowD, bez, bezAngle, ClipArt, flight, pathD, Pt, trail} from './shared';

export const DURATION = 60;

const POS = {
  browser: {x: 560, y: 380},
  storage: {x: 1360, y: 350},
  website: {x: 660, y: 720},
  queue: {x: 1280, y: 700},
};

const START: Pt = {x: 626, y: 360};
const CTRL: Pt = {x: 960, y: 130};
const END: Pt = {x: 1294, y: 337};
const WEB_R: Pt = {x: 716, y: 702};
const QUEUE_L: Pt = {x: 1224, y: 690};
const WEB_CTRL: Pt = {x: 960, y: 786};

const S = 160;
const ISO = 'rotateX(58deg) rotateZ(-45deg)';
const EXTRUDE = Array.from({length: 9}, (_, i) => `0 ${(i + 1) * 3.5}px 0 ${i === 8 ? '#0A0C0F' : '#0C0F13'}`).join(', ');

const Tile: React.FC<{
  x: number;
  y: number;
  label: string;
  service: string;
  active?: boolean;
}> = ({x, y, label, service, active}) => <>
  <div style={{
    position: 'absolute',
    left: x - 128,
    top: y - 4,
    width: 256,
    height: 96,
    borderRadius: '50%',
    background: 'radial-gradient(ellipse at center, rgba(0,0,0,.6), transparent 70%)',
    filter: 'blur(12px)',
  }} />
  {active ? <div style={{
    position: 'absolute',
    left: x - 150,
    top: y - 26,
    width: 300,
    height: 160,
    borderRadius: '50%',
    background: 'radial-gradient(ellipse at center, rgba(255,138,42,.22), transparent 68%)',
    filter: 'blur(18px)',
  }} /> : null}
  <div style={{
    position: 'absolute',
    left: x - S / 2,
    top: y - S / 2,
    width: S,
    height: S,
    borderRadius: 30,
    transform: ISO,
    background: active
      ? 'linear-gradient(145deg, #3E3328 0%, #241E1A 100%)'
      : 'linear-gradient(145deg, #2A3038 0%, #171A1F 100%)',
    border: active ? '1px solid rgba(255,138,42,.5)' : '1px solid rgba(255,255,255,.11)',
    boxShadow: `${active ? '0 0 46px rgba(255,138,42,.20), ' : ''}${EXTRUDE}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  }}>
    <div style={{
      width: 58,
      height: 6,
      borderRadius: 3,
      background: active ? 'rgba(255,182,126,.95)' : 'rgba(255,255,255,.30)',
      boxShadow: active ? '0 0 20px rgba(255,138,42,.8)' : 'none',
    }} />
  </div>
  <div style={{
    position: 'absolute',
    left: x - 160,
    top: y + 86,
    width: 320,
    textAlign: 'center',
    fontFamily,
  }}>
    <div style={{margin: '0 auto 12px', width: 1, height: 18, background: 'rgba(255,255,255,.16)'}} />
    <div style={{
      fontSize: 46,
      fontWeight: 600,
      letterSpacing: '-.02em',
      lineHeight: 1,
      color: C.text,
    }}>{label}</div>
    <div style={{
      marginTop: 11,
      fontSize: 24,
      letterSpacing: '.07em',
      color: active ? '#C39C7F' : '#89929C',
    }}>{service}</div>
  </div>
</>;

const pathToStorage = pathD(START, CTRL, END);
const pathToQueue = pathD(WEB_R, WEB_CTRL, QUEUE_L);

export const StyleD_Spatial: React.FC = () => {
  const f = useCurrentFrame();
  const t = flight(f);
  const at = bez(START, CTRL, END, t);
  const angle = bezAngle(START, CTRL, END, t);
  const ghosts = trail(START, CTRL, END, t, 4, 0.22);

  return <AbsoluteFill style={{
    background:
      'radial-gradient(1300px 900px at 50% 42%, #0B0E13 0%, #07090C 56%, #040507 100%)',
  }}>
    <div style={{
      position: 'absolute',
      left: '50%',
      top: '58%',
      width: 2800,
      height: 2800,
      transform: `translate(-50%, -50%) ${ISO}`,
      backgroundImage:
        'linear-gradient(rgba(139,166,194,.13) 1px, transparent 1px), linear-gradient(90deg, rgba(139,166,194,.13) 1px, transparent 1px)',
      backgroundSize: '84px 84px',
      border: '1px solid rgba(139,166,194,.10)',
      borderRadius: 80,
      maskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
      WebkitMaskImage: 'radial-gradient(closest-side, #000 0%, rgba(0,0,0,.86) 42%, transparent 76%)',
    }} />
    <div style={{
      position: 'absolute',
      left: '50%',
      top: '52%',
      width: 1500,
      height: 900,
      transform: 'translate(-50%, -50%)',
      background: 'radial-gradient(ellipse at center, rgba(66,88,118,.20), transparent 66%)',
      filter: 'blur(20px)',
    }} />

    <div style={{
      position: 'absolute',
      left: at.x - 240,
      top: at.y + 150,
      width: 480,
      height: 170,
      borderRadius: '50%',
      background: 'radial-gradient(ellipse at center, rgba(255,138,42,.26), transparent 68%)',
      filter: 'blur(20px)',
      transform: 'rotate(-8deg)',
    }} />

    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, overflow: 'visible'}}>
      <defs>
        <linearGradient id="d-hot" gradientUnits="userSpaceOnUse" x1={START.x} y1={START.y} x2={END.x} y2={END.y}>
          <stop offset="0" stopColor="#FF8A2A" stopOpacity=".2" />
          <stop offset=".5" stopColor="#FFBE88" stopOpacity=".95" />
          <stop offset="1" stopColor="#FF8A2A" stopOpacity=".45" />
        </linearGradient>
        <linearGradient id="d-cool" gradientUnits="userSpaceOnUse" x1={WEB_R.x} y1={WEB_R.y} x2={QUEUE_L.x} y2={QUEUE_L.y}>
          <stop offset="0" stopColor="#425165" stopOpacity=".5" />
          <stop offset="1" stopColor="#7589A3" stopOpacity=".8" />
        </linearGradient>
        <filter id="d-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="8" />
        </filter>
      </defs>

      <path d={pathToStorage} fill="none" stroke="rgba(255,138,42,.4)" strokeWidth={13} strokeLinecap="round" filter="url(#d-glow)" />
      <path d={pathToStorage} fill="none" stroke="url(#d-hot)" strokeWidth={2.6} strokeLinecap="round" />
      <path d={arrowD(END, bezAngle(START, CTRL, END, 1), 13)} fill="none" stroke="rgba(255,186,136,.9)" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />

      <path d={pathToQueue} fill="none" stroke="rgba(117,137,163,.16)" strokeWidth={9} strokeLinecap="round" filter="url(#d-glow)" />
      <path d={pathToQueue} fill="none" stroke="url(#d-cool)" strokeWidth={1.9} strokeLinecap="round" />
      <path d={arrowD(QUEUE_L, bezAngle(WEB_R, WEB_CTRL, QUEUE_L, 1), 11)} fill="none" stroke="rgba(150,170,196,.7)" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" />
    </svg>

    <Tile x={POS.browser.x} y={POS.browser.y} label="Browser" service="the file" active />
    <Tile x={POS.storage.x} y={POS.storage.y} label="Storage" service="S3" active />
    <Tile x={POS.website.x} y={POS.website.y} label="Website" service="the app" />
    <Tile x={POS.queue.x} y={POS.queue.y} label="Queue" service="SQS" />

    <div style={{
      position: 'absolute',
      left: at.x - 150,
      top: at.y - 150,
      width: 300,
      height: 300,
      borderRadius: '50%',
      background: 'radial-gradient(circle, rgba(255,138,42,.24), transparent 66%)',
      filter: 'blur(16px)',
    }} />

    {ghosts.map((g, i) => <ClipArt
      key={i}
      width={78 * (0.72 + 0.28 * g.k)}
      radius={14}
      border="1px solid rgba(255,255,255,.12)"
      style={{
        position: 'absolute',
        left: g.p.x - (78 * (0.72 + 0.28 * g.k)) / 2,
        top: g.p.y - ((78 * (0.72 + 0.28 * g.k)) * 8) / 9,
        opacity: g.k * 0.22,
        transform: `rotate(${-5 + angle * 0.13}deg)`,
        filter: 'blur(1.5px)',
      }}
    />)}

    <div style={{
      position: 'absolute',
      left: at.x - 39,
      top: at.y - 69,
      transform: `rotate(${-5 + angle * 0.13}deg)`,
    }}>
      <ClipArt
        width={78}
        radius={15}
        border="1.5px solid rgba(255,255,255,.72)"
        style={{
          boxShadow: '0 0 0 1px rgba(255,138,42,.4), 0 26px 56px rgba(0,0,0,.8), 0 0 60px rgba(255,138,42,.5)',
        }}
      />
    </div>
  </AbsoluteFill>;
};
