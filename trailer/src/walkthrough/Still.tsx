import React from 'react';
import {
  AbsoluteFill, Easing, Img, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {Fade} from './Panel';
import {Cam, FADE, FPS, Still, sec} from './timeline';

const GLIDE = 1.2;
const FRAME_W = 1920;
const FRAME_H = 1080;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

const camAt = (cams: Cam[], t: number) => {
  let a = cams[0];
  for (let i = 0; i < cams.length - 1; i++) {
    const b = cams[i + 1];
    if (t < b.at) return a;
    if (t <= b.at + GLIDE) {
      const p = interpolate(t, [b.at, b.at + GLIDE], [0, 1], {
        extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        easing: Easing.inOut(Easing.cubic),
      });
      return {
        cx: lerp(a.cx, b.cx, p),
        cy: lerp(a.cy, b.cy, p),
        zoom: Math.exp(lerp(Math.log(a.zoom), Math.log(b.zoom), p)),
      };
    }
    a = b;
  }
  return a;
};

const StillContent: React.FC<{still: Still}> = ({still}) => {
  const f = useCurrentFrame();
  if (!still.w || !still.h) throw new Error(`Still ${still.id} has no size`);

  const t = f / FPS;
  const cam = camAt(still.cams, t);
  const fit = Math.min(1760 / still.w, 940 / still.h);
  const scale = fit * cam.zoom;
  const left = FRAME_W / 2 - cam.cx * still.w * scale;
  const top = FRAME_H / 2 - cam.cy * still.h * scale;

  const spot = (still.spots ?? []).find((s) => t >= s.from && t <= s.to);
  let spotlight: React.ReactNode = null;
  if (spot) {
    const fadeIn = interpolate(t, [spot.from, spot.from + 0.35], [0, 1], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    });
    const fadeOut = interpolate(t, [spot.to - 0.35, spot.to], [1, 0], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    });
    const x = left + spot.x * still.w * scale;
    const y = top + spot.y * still.h * scale;
    const w = spot.w * still.w * scale;
    const h = spot.h * still.h * scale;
    spotlight = <div style={{
      position: 'absolute', left: x - 10, top: y - 10, width: w + 20, height: h + 20,
      borderRadius: 12, border: '3px solid #FF8A2A',
      boxShadow: '0 0 0 9999px rgba(0,0,0,.45), 0 0 28px rgba(255,138,42,.45)',
      opacity: Math.min(fadeIn, fadeOut),
    }} />;
  }

  return <AbsoluteFill>
    <Img
      src={staticFile(still.img)}
      style={{
        position: 'absolute', left: 0, top: 0, width: still.w, height: still.h,
        transform: `translate(${left}px, ${top}px) scale(${scale})`,
        transformOrigin: '0 0',
        borderRadius: 14 / scale,
        boxShadow: '0 40px 120px rgba(0,0,0,.85)',
      }}
    />
    {spotlight}
  </AbsoluteFill>;
};

export const StillLayer: React.FC<{still: Still}> = ({still}) => {
  const total = sec(still.dur);
  return <Sequence from={sec(still.start) - FADE / 2} durationInFrames={total + FADE}>
    <Fade total={total}>
      <Sequence from={FADE / 2} durationInFrames={total + FADE / 2}>
        <StillContent still={still} />
      </Sequence>
    </Fade>
  </Sequence>;
};
