import React from 'react';
import {Easing, interpolate, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {ScreenWindow, SourceMark} from './MacWindow';
import {FPS, SCENES, at} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

export const Upload: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const chipAt = at(3) - SCENES.upload.start + 0.15;
  const chip = interpolate(t, [chipAt, chipAt + 0.35], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  return <ScreenWindow
    title="NeuroLens — Upload"
    x={260} y={140} w={1400} h={880}
    src="walkthrough/captures/upload_dialog.png"
    kind="image"
    sourceW={2940} sourceH={1912}
    base={{x: 980, y: 800, w: 1020, h: 560}}
    cams={[
      {at: 0, cx: 1490, cy: 1080, zoom: 1},
    ]}
  >
    <SourceMark
      x={1058} y={1194} w={790} h={50}
      opacity={t > 0.4 && t < 8.4 ? 1 : 0}
      radius={12}
    />
    <div style={{
      position: 'absolute', left: 1600, top: 1240,
      height: 44, padding: '0 18px', boxSizing: 'border-box',
      display: 'flex', alignItems: 'center', gap: 10,
      borderRadius: 999, background: 'rgba(255,138,42,.13)',
      border: `1px solid ${C.hot}`, color: C.hot,
      fontFamily, fontSize: 25, fontWeight: 600, letterSpacing: '-0.01em',
      opacity: chip, transform: `translateY(${(1 - chip) * 6}px)`,
      boxShadow: '0 8px 26px rgba(255,138,42,.18)',
      whiteSpace: 'nowrap',
    }}>
      <span style={{width: 8, height: 8, borderRadius: 999, background: C.hot, display: 'block'}} />
      held
    </div>
  </ScreenWindow>;
};

export const UploadCaption: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const show = clamp((t - 0.05) / 0.4);
  return <div style={{
    position: 'absolute', left: 270, top: 64,
    fontFamily, fontSize: 18, fontWeight: 500, letterSpacing: '.14em',
    textTransform: 'uppercase', color: C.muted, opacity: show,
  }}>One upload · one price</div>;
};
