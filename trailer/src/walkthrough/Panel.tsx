import React from 'react';
import {
  AbsoluteFill, Easing, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {C} from '../edit/ui';
import {
  FADE, FPS, HEIGHT, PANEL_MAX_H, PANEL_MAX_W, Rect, Shot, SOURCE_H, SOURCE_W, TOP_CROP,
  WIDTH, sec,
} from './timeline';

const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));

const fitPanel = (aspect: number) => {
  let w = PANEL_MAX_W;
  let h = w / aspect;
  if (h > PANEL_MAX_H) {
    h = PANEL_MAX_H;
    w = h * aspect;
  }
  return {w, h};
};

const cropAt = (shot: Shot, p: number) => {
  const states = shot.states;
  const at = clamp(p, 0, 1);
  let a = states[0];
  let b = states[states.length - 1];
  for (let i = 0; i < states.length - 1; i++) {
    if (at >= states[i].at && at <= states[i + 1].at) {
      a = states[i];
      b = states[i + 1];
      break;
    }
  }
  const mix = (x: number, y: number) =>
    interpolate(at, [a.at, b.at], [x, y], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
      easing: Easing.inOut(Easing.cubic),
    });
  const zoom = mix(a.zoom, b.zoom);
  const w = shot.base.w / zoom;
  const h = shot.base.h / zoom;
  const cx = mix(a.cx, b.cx);
  const cy = mix(a.cy, b.cy);
  const x = clamp(cx - w / 2, 0, SOURCE_W - w);
  const y = clamp(cy - h / 2, TOP_CROP, SOURCE_H - h);
  return {x, y, w, h};
};

const sourceToPanel = (x: number, y: number, crop: Rect, panel: {w: number; h: number}) => ({
  x: (x - crop.x) * (panel.w / crop.w),
  y: (y - crop.y) * (panel.h / crop.h),
});

const sourceTimeToLocal = (shot: Shot, t: number) => {
  let acc = 0;
  for (const s of shot.segments) {
    const end = s.sourceStart + (s.frames / FPS) * s.rate;
    if (t >= s.sourceStart && t <= end) {
      return acc + ((t - s.sourceStart) / s.rate) * FPS;
    }
    acc += s.frames;
  }
  return null;
};

const Ring: React.FC<{
  f: number; shot: Shot; crop: Rect; panel: {w: number; h: number};
}> = ({f, shot, crop, panel}) => {
  const ring = (shot.rings ?? []).map((r) => ({r, local: sourceTimeToLocal(shot, r.sourceTime)}))
    .find(({local}) => local !== null && f >= local && f < local + 15);
  if (!ring || ring.local === null) return null;
  const age = clamp((f - ring.local) / 15, 0, 1);
  const pos = sourceToPanel(ring.r.x, ring.r.y, crop, panel);
  const size = 60;
  return <div style={{
    position: 'absolute', left: pos.x, top: pos.y, width: size, height: size,
    transform: `translate(-50%, -50%) scale(${0.08 + age * 0.92})`,
    borderRadius: '50%', border: `3px solid ${C.hot}`,
    opacity: (1 - age) * 0.92,
    boxShadow: '0 0 20px rgba(255,138,42,.5)',
  }} />;
};

const ShotContent: React.FC<{shot: Shot; total: number}> = ({shot, total}) => {
  const f = useCurrentFrame();
  const p = total <= 1 ? 0 : clamp(f / (total - 1), 0, 1);
  const crop = cropAt(shot, p);
  const panel = fitPanel(shot.base.w / shot.base.h);
  const scaleX = panel.w / crop.w;
  const scaleY = panel.h / crop.h;
  let acc = 0;
  return <AbsoluteFill>
    <div style={{
      position: 'absolute', left: (WIDTH - panel.w) / 2, top: (HEIGHT - panel.h) / 2,
      width: panel.w, height: panel.h, borderRadius: 18, overflow: 'hidden',
      boxShadow: '0 40px 120px rgba(0,0,0,.9), 0 0 0 1px rgba(255,255,255,.06)',
      background: '#000',
    }}>
      {shot.segments.map((s, i) => {
        const from = acc;
        acc += s.frames;
        return <Sequence key={i} from={from} durationInFrames={s.frames + (i === shot.segments.length - 1 ? FADE / 2 : 0)}>
          <OffthreadVideo
            src={staticFile(shot.src)}
            trimBefore={sec(s.sourceStart)}
            playbackRate={s.rate}
            muted
            style={{
              position: 'absolute',
              width: SOURCE_W * scaleX,
              height: SOURCE_H * scaleY,
              left: -crop.x * scaleX,
              top: -crop.y * scaleY,
            }}
          />
        </Sequence>;
      })}
      <Ring f={f} shot={shot} crop={crop} panel={panel} />
      <div style={{position: 'absolute', inset: 0, borderRadius: 18, boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.05)'}} />
    </div>
  </AbsoluteFill>;
};

export const Fade: React.FC<{total: number; background?: string; children: React.ReactNode}> = ({total, background = 'transparent', children}) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, FADE, total + FADE - FADE, total + FADE], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  return <AbsoluteFill style={{
    opacity,
    background,
  }}>{children}</AbsoluteFill>;
};

export const ShotLayer: React.FC<{shot: Shot}> = ({shot}) => {
  const total = shot.segments.reduce((a, s) => a + s.frames, 0);
  return <Sequence from={sec(shot.start) - FADE / 2} durationInFrames={total + FADE}>
    <Fade total={total}>
      <Sequence from={FADE / 2} durationInFrames={total + FADE / 2}>
        <ShotContent shot={shot} total={total} />
      </Sequence>
    </Fade>
  </Sequence>;
};
