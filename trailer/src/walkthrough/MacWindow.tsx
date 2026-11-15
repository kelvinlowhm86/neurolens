import React from 'react';
import {
  Easing, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {fontFamily} from '../edit/ui';
import {FPS, sec} from './timeline';

export type Rect = {x: number; y: number; w: number; h: number};
export type ScreenCam = {at: number; cx: number; cy: number; zoom: number};

const GLIDE = 1.1;
const BAR = 34;
const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const cropAt = (base: Rect, cams: ScreenCam[] | undefined, t: number, sw: number, sh: number): Rect => {
  if (!cams || cams.length === 0) return base;
  let a = cams[0];
  let active = cams[0];
  for (let i = 0; i < cams.length - 1; i++) {
    const b = cams[i + 1];
    if (t < b.at) break;
    if (t <= b.at + GLIDE) {
      const p = interpolate(t, [b.at, b.at + GLIDE], [0, 1], {
        extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        easing: Easing.inOut(Easing.cubic),
      });
      active = {
        at: 0,
        cx: lerp(a.cx, b.cx, p),
        cy: lerp(a.cy, b.cy, p),
        zoom: Math.exp(lerp(Math.log(a.zoom), Math.log(b.zoom), p)),
      };
      a = b;
      const w = base.w / active.zoom;
      const h = base.h / active.zoom;
      return {
        x: clamp(active.cx - w / 2, 0, Math.max(0, sw - w)),
        y: clamp(active.cy - h / 2, 0, Math.max(0, sh - h)),
        w, h,
      };
    }
    a = b;
    active = b;
  }
  const w = base.w / active.zoom;
  const h = base.h / active.zoom;
  return {
    x: clamp(active.cx - w / 2, 0, Math.max(0, sw - w)),
    y: clamp(active.cy - h / 2, 0, Math.max(0, sh - h)),
    w, h,
  };
};

export const MacChrome: React.FC<{title: string; children: React.ReactNode}> = ({title, children}) => (
  <div style={{
    position: 'absolute', inset: 0, borderRadius: 18, overflow: 'hidden',
    background: '#0D0E11',
    boxShadow: '0 50px 140px rgba(0,0,0,.9), 0 0 0 1px rgba(255,255,255,.07)',
  }}>
    <div style={{
      position: 'absolute', left: 0, right: 0, top: 0, height: BAR,
      background: 'linear-gradient(180deg, #23252A 0%, #17181C 100%)',
      borderBottom: '1px solid rgba(255,255,255,.06)',
      display: 'flex', alignItems: 'center', padding: '0 16px', boxSizing: 'border-box',
      fontFamily,
    }}>
      <div style={{display: 'flex', gap: 8, alignItems: 'center'}}>
        {['#FF5F57', '#FEBC2E', '#28C840'].map((color) => (
          <span key={color} style={{
            width: 11, height: 11, borderRadius: 999, background: color,
            boxShadow: 'inset 0 0 0 .5px rgba(0,0,0,.25)',
          }} />
        ))}
      </div>
      <div style={{
        position: 'absolute', left: 0, right: 0, textAlign: 'center',
        fontSize: 14, fontWeight: 500, letterSpacing: '-0.005em', color: 'rgba(236,237,239,.62)',
        pointerEvents: 'none',
      }}>{title}</div>
    </div>
    <div style={{position: 'absolute', left: 0, right: 0, top: BAR, bottom: 0, overflow: 'hidden', background: '#000'}}>
      {children}
    </div>
  </div>
);

export const ScreenWindow: React.FC<{
  title: string;
  x: number; y: number; w: number; h: number;
  src: string;
  kind: 'video' | 'image';
  sourceW: number; sourceH: number;
  sourceStart?: number;
  rate?: number;
  base: Rect;
  cams?: ScreenCam[];
  fit?: 'cover' | 'contain';
  contentBg?: string;
  children?: React.ReactNode;
}> = ({title, x, y, w, h, src, kind, sourceW, sourceH, sourceStart = 0, rate = 1, base, cams, fit = 'cover', contentBg = '#000', children}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const crop = cropAt(base, cams, t, sourceW, sourceH);
  const contentH = h - BAR;
  const scale = fit === 'contain'
    ? Math.min(w / crop.w, contentH / crop.h)
    : Math.max(w / crop.w, contentH / crop.h);
  const offX = (w - crop.w * scale) / 2;
  const offY = (contentH - crop.h * scale) / 2;

  return <div style={{position: 'absolute', left: x, top: y, width: w, height: h}}>
    <MacChrome title={title}>
      <div style={{position: 'absolute', inset: 0, background: contentBg}} />
      <div style={{
        position: 'absolute',
        left: offX - crop.x * scale,
        top: offY - crop.y * scale,
        width: sourceW * scale,
        height: sourceH * scale,
        overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', left: 0, top: 0, width: sourceW, height: sourceH,
          transform: `scale(${scale})`, transformOrigin: '0 0',
        }}>
          {kind === 'video'
            ? <OffthreadVideo
              src={staticFile(src)}
              trimBefore={sec(sourceStart)}
              playbackRate={rate}
              muted
              style={{position: 'absolute', left: 0, top: 0, width: sourceW, height: sourceH}}
            />
            : <Img
              src={staticFile(src)}
              style={{position: 'absolute', left: 0, top: 0, width: sourceW, height: sourceH}}
            />}
          {children}
        </div>
      </div>
    </MacChrome>
  </div>;
};

export const SourceMark: React.FC<{
  x: number; y: number; w: number; h: number; opacity?: number; radius?: number;
}> = ({x, y, w, h, opacity = 1, radius = 10}) => (
  <div style={{
    position: 'absolute', left: x - 8, top: y - 8, width: w + 16, height: h + 16,
    border: '3px solid #FF8A2A', borderRadius: radius,
    boxShadow: '0 0 0 9999px rgba(0,0,0,.42), 0 0 26px rgba(255,138,42,.5)',
    opacity, boxSizing: 'border-box', pointerEvents: 'none',
  }} />
);
