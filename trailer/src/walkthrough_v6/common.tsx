import React from 'react';
import {ThreeCanvas} from '@remotion/three';
import {AbsoluteFill, Easing, OffthreadVideo, interpolate, staticFile} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Cortex} from '../scenes/brain/Cortex';
import {useSweeBrain} from '../walkthrough/brainData';
import {SWEE} from '../walkthrough/timeline';

// Shared by the v6 intro, result and outro comps. Same look as the diagram comp
// (Diagram37.tsx): flat #0B0C0F, Geist, orange accent, one bright thing per shot.

export const FPS = 30;
export const BG = '#0B0C0F';
export const STRIP_H = 110;
export const STRIP_TOP = 1080 - STRIP_H;
export const SAFE = 80;

export const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export const easeOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});

export const easeInOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)});

export const easeBack = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.back(1.6))});

export const timecode = (second: number) => {
  const whole = Math.max(0, Math.floor(second));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

export const Stage: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill style={{background: BG, fontFamily, color: C.text, overflow: 'hidden'}}>{children}</AbsoluteFill>
);

/** The clip in a phone-shaped frame. `from` is the second of the clip shown at the first frame. */
export const PhoneClip: React.FC<{
  x: number; y: number; w: number; from?: number; opacity?: number; scale?: number; glow?: number;
  time?: number;
}> = ({x, y, w, from = 0, opacity = 1, scale = 1, glow = 0, time}) => {
  const h = Math.round((w * 16) / 9);
  return (
    <div style={{
      position: 'absolute', left: x, top: y, width: w, height: h,
      borderRadius: Math.round(w * 0.09), overflow: 'hidden', background: '#000', boxSizing: 'border-box',
      border: '1.5px solid rgba(255,255,255,.16)',
      boxShadow: `0 0 ${60 * glow}px rgba(255,138,42,${0.4 * glow})`,
      opacity, transform: `scale(${scale})`, transformOrigin: 'center center', zIndex: 8,
    }}>
      <OffthreadVideo
        src={staticFile(SWEE.video)} muted trimBefore={Math.round(from * FPS)}
        style={{width: '100%', height: '100%', objectFit: 'cover', display: 'block'}}
      />
      {time !== undefined ? (
        <div style={{
          position: 'absolute', left: 18, bottom: 18, padding: '4px 16px 6px', borderRadius: 12,
          background: 'rgba(0,0,0,.66)', fontSize: 44, fontWeight: 500, color: C.text,
          fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em',
        }}>{timecode(time)}</div>
      ) : null}
    </div>
  );
};

/** The cortex lit for one second of the clip. Renders nothing while the data loads. */
export const BrainView: React.FC<{
  x: number; y: number; size: number; second: number; opacity?: number; progress?: number;
}> = ({x, y, size, second, opacity = 1, progress = 0.2}) => {
  const data = useSweeBrain();
  return (
    <div style={{position: 'absolute', left: x, top: y, width: size, height: size, opacity}}>
      <div style={{
        position: 'absolute', inset: -size * 0.2,
        background: 'radial-gradient(closest-side, rgba(255,138,42,.16), rgba(255,138,42,0) 72%)',
      }} />
      {data ? (
        <ThreeCanvas
          width={640} height={640}
          style={{position: 'absolute', left: '50%', top: '50%', transform: `translate(-50%, -50%) scale(${(size / 640) * 1.4})`}}
          camera={{fov: 28, near: 0.1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}}
          gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}}
          dpr={1}
        >
          <Cortex data={data} second={clamp(second, 0, data.count - 1)} progress={progress} />
        </ThreeCanvas>
      ) : null}
    </div>
  );
};

export const Cursor: React.FC<{x: number; y: number; opacity: number; pressed: boolean}> = ({x, y, opacity, pressed}) => (
  <div style={{
    position: 'absolute', left: x, top: y, opacity, pointerEvents: 'none',
    transform: `scale(${pressed ? 0.86 : 1})`, transformOrigin: 'top left',
    filter: 'drop-shadow(0 8px 14px rgba(0,0,0,.55))', zIndex: 40,
  }}>
    <svg width={46} height={58} viewBox="0 0 46 58" aria-hidden>
      <path d="M4 3 L4 45 L15 34 L24 55 L33 50 L24 30 L40 30 Z" fill="#ECEDEF" stroke="#0A0B0D" strokeWidth={3} strokeLinejoin="round" />
    </svg>
  </div>
);

export const Ring: React.FC<{x: number; y: number; p: number}> = ({x, y, p}) => {
  if (p <= 0 || p >= 1) return null;
  const r = 12 + p * 66;
  return <div style={{
    position: 'absolute', left: x - r, top: y - r, width: r * 2, height: r * 2, borderRadius: 999,
    border: `3px solid ${C.hot}`, opacity: (1 - p) * 0.6, pointerEvents: 'none', zIndex: 39,
  }} />;
};

/** Small orange signpost words, same pill as the diagram's captions. */
export const Signpost: React.FC<{text: string; y: number; opacity: number}> = ({text, y, opacity}) => (
  <div style={{
    position: 'absolute', left: 0, right: 0, top: y, display: 'flex', justifyContent: 'center',
    pointerEvents: 'none', opacity,
  }}>
    <div style={{
      fontSize: 44, fontWeight: 600, letterSpacing: '.005em', color: C.hot,
      background: 'rgba(8,9,11,.8)', border: `1px solid ${C.rule}`, borderRadius: 999,
      padding: '15px 38px 17px', whiteSpace: 'nowrap',
    }}>{text}</div>
  </div>
);

/** The credit strip along the bottom, as in the diagram: solid, with a hatched held slice. */
export const CreditStrip: React.FC<{opacity: number; held: number; status: string; lit?: boolean; label?: string; holdShare?: number}> = ({
  opacity,
  held,
  status,
  lit = false,
  label = '5 min credit',
  holdShare = 0.2,
}) => {
  if (opacity <= 0.001) return null;
  const BAR_W = 900;
  const BAR_H = 18;
  // `held` animates the hold on; `holdShare` is the held fraction of the 5-minute bar (0.5 min = 0.1).
  const hold = holdShare * held;
  return (
    <div style={{
      position: 'absolute', left: 0, right: 0, top: STRIP_TOP, height: STRIP_H, boxSizing: 'border-box',
      background: 'rgba(11,12,15,.9)', borderTop: `1.5px solid ${lit ? 'rgba(255,158,84,.5)' : C.rule}`,
      boxShadow: lit ? '0 -10px 34px rgba(255,138,42,.1)' : 'none', opacity,
    }}>
      <div style={{
        position: 'absolute', left: SAFE, top: 0, bottom: 0, display: 'flex', alignItems: 'center',
        fontSize: 34, fontWeight: 600, letterSpacing: '-.01em', color: '#E4E9EF', whiteSpace: 'nowrap',
      }}>{label}</div>
      <div style={{
        position: 'absolute', left: 960 - BAR_W / 2, top: '50%', marginTop: -BAR_H / 2, width: BAR_W, height: BAR_H,
        borderRadius: BAR_H / 2, background: '#232830', overflow: 'hidden',
      }}>
        <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: `${(1 - hold) * 100}%`, background: C.hot, opacity: 0.82}} />
        {hold > 0.001 ? (
          <div style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: `${hold * 100}%`,
            background: 'repeating-linear-gradient(135deg, rgba(255,158,84,.5) 0 5px, rgba(255,158,84,.08) 5px 11px)',
            borderLeft: '2px solid rgba(255,158,84,.6)', boxSizing: 'border-box',
          }} />
        ) : null}
      </div>
      <div style={{
        position: 'absolute', right: SAFE, top: 0, bottom: 0, display: 'flex', alignItems: 'center',
        fontSize: 34, fontWeight: 600, letterSpacing: '-.01em', color: lit ? '#FFC08A' : '#D6DCE4', whiteSpace: 'nowrap',
      }}>{status}</div>
    </div>
  );
};

export const SpeakerIcon: React.FC<{muted: boolean; colour: string; size?: number}> = ({muted, colour, size = 56}) => (
  <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden>
    <path d="M5 16 H11 L19 9 V31 L11 24 H5 Z" fill="none" stroke={colour} strokeWidth={2.6} strokeLinejoin="round" />
    {muted
      ? <line x1={24} y1={14} x2={34} y2={26} stroke={colour} strokeWidth={2.6} strokeLinecap="round" />
      : <>
          <path d="M23 15 C26 18 26 22 23 25" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
          <path d="M27 11 C32 16 32 24 27 29" fill="none" stroke={colour} strokeWidth={2.4} strokeLinecap="round" />
        </>}
  </svg>
);
