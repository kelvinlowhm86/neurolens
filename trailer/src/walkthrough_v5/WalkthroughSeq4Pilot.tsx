import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Cursor} from './Cursor';
import {Spotlight} from './Spotlight';
import {Stage} from './Stage';
import {
  CLICKS, DURATION, DURATION_S, FPS, LABELS, LINES, MUSIC_OFFSET, MUSIC_SOURCE, SFX,
  START, VOICE_SOURCE, at, sec,
} from './timing';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const VOICE = LINES.filter((l) => l.line >= 8 && l.line <= 14);

const musicLevel = (t: number) => {
  let duck = 0;
  const ramp = 0.38;
  for (const line of VOICE) {
    const start = line.start - START;
    const end = line.end - START;
    const d = t < start
      ? clamp((t - (start - ramp)) / ramp)
      : t > end
        ? 1 - clamp((t - end) / ramp)
        : 1;
    duck = Math.max(duck, d);
  }
  const bed = 0.22;
  const under = 0.095;
  const level = bed + (under - bed) * duck;
  return level * clamp(t / 0.8) * clamp((DURATION_S - t) / 1.0);
};

const Chip: React.FC<{
  from: number; to: number; text: string; x: number; y: number; align?: 'left' | 'right';
}> = ({from, to, text, x, y, align = 'left'}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const opacity = Math.min(clamp((t - from) / 0.45), clamp((to - t) / 0.45));
  return <div style={{
    position: 'absolute',
    left: x,
    top: y,
    transform: align === 'right' ? 'translateX(-100%)' : undefined,
    opacity,
    fontFamily,
    fontSize: 18,
    fontWeight: 500,
    letterSpacing: '.02em',
    color: 'rgba(236,237,239,.66)',
    whiteSpace: 'nowrap',
    pointerEvents: 'none',
  }}>{text}</div>;
};

const Chapter: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = Math.min(clamp(frame / 12), clamp((DURATION - 1 - frame) / 12)) * 0.8;
  return <div style={{
    position: 'absolute',
    left: 150,
    top: 17,
    opacity,
    fontFamily,
    fontSize: 17,
    fontWeight: 500,
    letterSpacing: '.17em',
    textTransform: 'uppercase',
    color: C.muted,
    pointerEvents: 'none',
  }}>The result</div>;
};

export const WalkthroughSeq4Pilot: React.FC = () => {
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, #0A0B0D 0%, #000 72%)', color: C.text, fontFamily}}>
    <Audio src={staticFile(VOICE_SOURCE)} trimBefore={sec(START)} volume={1} />
    <Audio
      src={staticFile(MUSIC_SOURCE)}
      trimBefore={sec(MUSIC_OFFSET)}
      volume={(f) => musicLevel(f / FPS)}
    />
    <Sequence from={0} durationInFrames={sec(2.2)}>
      <Audio src={staticFile(SFX.swell)} volume={0.18} />
    </Sequence>
    {CLICKS.map((click, i) => <Sequence key={`tap-${i}`} from={sec(click.at)} durationInFrames={20}>
      <Audio src={staticFile(SFX.tap)} volume={0.2} />
    </Sequence>)}

    <Stage />
    <Spotlight from={at(12) + 2.6} to={at(12) + 8.1} sourceTop={1338} sourceBottom={1612} />
    <Spotlight from={at(14) + 1.45} to={DURATION_S - 0.6} sourceTop={1348} sourceBottom={1412} strength={0.5} />
    <Cursor />

    <Chapter />
    <Chip from={LABELS.run.from} to={LABELS.run.to} text={LABELS.run.text} x={1770} y={20} align="right" />
    <Chip from={LABELS.bedrock.from} to={LABELS.bedrock.to} text={LABELS.bedrock.text} x={150} y={1024} />
    <Chip from={LABELS.compare.from} to={LABELS.compare.to} text={LABELS.compare.text} x={150} y={1024} />
  </AbsoluteFill>;
};
