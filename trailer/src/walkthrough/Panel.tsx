import React from 'react';
import {
  AbsoluteFill, Easing, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {
  FADE, FPS, HEIGHT, PANEL_MAX_H, PANEL_MAX_W, Shot, SOURCE_H, SOURCE_W, TOP_CROP,
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

const ShotContent: React.FC<{shot: Shot; total: number}> = ({shot, total}) => {
  const f = useCurrentFrame();
  // Hold ~1 s at each end; the eased move happens in between.
  const hold = Math.min(FPS, Math.floor(total / 4));
  const p = total <= 2 * hold + 1 ? 0 : clamp((f - hold) / (total - 1 - 2 * hold), 0, 1);
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
