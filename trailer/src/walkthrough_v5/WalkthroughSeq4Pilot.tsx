import React from 'react';
import {AbsoluteFill, Audio, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Cursor} from './Cursor';
import {FocusSpotlight, Spotlight, glideRect} from './Spotlight';
import {Stage} from './Stage';
import {
  CLICKS, DURATION, DURATION_S, FOCUS_CHART, FOCUS_COMPARE, FOCUS_DROP,
  FOCUS_PEAK, FOCUS_REPORT, FOCUS_SOUND_LABEL, FOCUS_TYPICAL, FPS, INSET,
  LABELS, LINES, MIDDLE_AT, MUSIC_OFFSET, MUSIC_SOURCE, SFX, STAGE, START,
  VOICE_SOURCE, at, sec,
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

const Label: React.FC<{from: number; to: number; text: string}> = ({from, to, text}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const opacity = Math.min(clamp((t - from) / 0.5), clamp((to - t) / 0.5));
  if (opacity <= 0) return null;
  return <>
    <div style={{
      position: 'absolute',
      left: STAGE.x,
      top: STAGE.y + STAGE.h - 150,
      width: STAGE.w,
      height: 150,
      background: 'linear-gradient(to top, rgba(0,0,0,.86) 0%, rgba(0,0,0,.55) 45%, rgba(0,0,0,0) 100%)',
      opacity,
    }} />
    <div style={{
      position: 'absolute',
      left: STAGE.x + 30,
      top: STAGE.y + STAGE.h - 54,
      opacity,
      fontFamily,
      fontSize: 24,
      fontWeight: 500,
      letterSpacing: '.01em',
      color: 'rgba(236,237,239,.95)',
      textShadow: '0 2px 10px rgba(0,0,0,.65)',
      whiteSpace: 'nowrap',
      pointerEvents: 'none',
    }}>{text}</div>
  </>;
};

const RunChip: React.FC<{from: number; to: number; text: string}> = ({from, to, text}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const opacity = Math.min(clamp((t - from) / 0.45), clamp((to - t) / 0.45));
  if (opacity <= 0) return null;
  return <div style={{
    position: 'absolute',
    left: STAGE.x + STAGE.w - 24,
    top: STAGE.y + 7,
    transform: 'translateX(-100%)',
    opacity,
    fontFamily,
    fontSize: 22,
    fontWeight: 500,
    letterSpacing: '.01em',
    color: 'rgba(236,237,239,.7)',
    whiteSpace: 'nowrap',
    pointerEvents: 'none',
  }}>{text}</div>;
};

const InsetVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const duration = INSET.to - INSET.from;
  const opacity = Math.min(clamp(t / 0.4), clamp((duration - t) / 0.6));
  if (opacity <= 0) return null;
  return <div style={{
    position: 'absolute',
    left: STAGE.x + STAGE.w - 24 - 300,
    // Below the "Well above typical" headline and its time note.
    top: STAGE.y + 210,
    width: 300,
    height: 533,
    borderRadius: 16,
    overflow: 'hidden',
    background: '#000',
    border: '1px solid rgba(255,255,255,.14)',
    boxShadow: '0 30px 80px rgba(0,0,0,.78), 0 0 0 1px rgba(0,0,0,.6)',
    opacity,
  }}>
    <OffthreadVideo
      src={staticFile(INSET.src)}
      trimBefore={sec(INSET.trim)}
      playbackRate={INSET.rate}
      muted
      style={{position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover'}}
    />
  </div>;
};

// The compare chart has no readable legend in the recording, so the two lines
// carry tiny A/B labels. They sit in the card's empty top band, pinned to the
// window (not the page) so they stay put while the camera pans along the chart.
const ChartLabels: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const from = at(14) + 0.15;
  const to = DURATION_S - 0.45;
  const opacity = Math.min(clamp((t - from) / 0.4), clamp((to - t) / 0.5));
  if (opacity <= 0) return null;
  const badge: React.CSSProperties = {
    position: 'absolute',
    left: STAGE.x + 640,
    fontFamily,
    fontSize: 19,
    fontWeight: 600,
    lineHeight: 1.1,
    whiteSpace: 'nowrap',
    padding: '4px 8px',
    borderRadius: 6,
    background: 'rgba(0,0,0,.55)',
    pointerEvents: 'none',
    opacity,
  };
  return <>
    <div style={{...badge, top: STAGE.y + 140, color: '#F0A16A'}}>A: short cut</div>
    <div style={{...badge, top: STAGE.y + 182, color: '#90B4EE'}}>B: long cut</div>
  </>;
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
    <ChartLabels />
    <Spotlight from={at(8)} to={at(9) - 0.15} sourceTop={504} sourceBottom={1295} strength={0.82} />
    <FocusSpotlight {...FOCUS_TYPICAL} />
    <Spotlight from={at(9) + 0.1} to={at(10) - 0.35} sourceTop={1327} sourceBottom={1856} strength={0.6} />
    <FocusSpotlight {...FOCUS_PEAK} />
    <FocusSpotlight {...FOCUS_CHART} />
    <FocusSpotlight {...FOCUS_DROP} />
    <FocusSpotlight {...FOCUS_SOUND_LABEL} />
    <FocusSpotlight {...FOCUS_REPORT} />
    <FocusSpotlight
      from={FOCUS_COMPARE.from}
      to={FOCUS_COMPARE.to}
      strength={FOCUS_COMPARE.strength}
      rectAt={(t) => glideRect(t, MIDDLE_AT - 0.3, MIDDLE_AT + 0.4,
        FOCUS_COMPARE.opening, FOCUS_COMPARE.middle)}
    />
    <Spotlight from={at(13) - 0.3} to={at(14) - 0.25} sourceTop={482} sourceBottom={1105} strength={0.5} />
    <Cursor />
    <Sequence from={sec(INSET.from)} durationInFrames={sec(INSET.to - INSET.from)}>
      <InsetVideo />
    </Sequence>

    <Chapter />
    <RunChip from={LABELS.run.from} to={LABELS.run.to} text={LABELS.run.text} />
    <Label from={LABELS.bedrock.from} to={LABELS.bedrock.to} text={LABELS.bedrock.text} />
    <Label from={LABELS.compare.from} to={LABELS.compare.to} text={LABELS.compare.text} />
  </AbsoluteFill>;
};
