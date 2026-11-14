import React from 'react';
import {
  AbsoluteFill, Audio, Easing, Sequence, interpolate, staticFile, useCurrentFrame,
} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Diagram} from './Diagram';
import {Fade, ShotLayer, sourceTimeToLocal} from './Panel';
import {
  CARDS, Card, DIAGRAM_POPS, DURATION_S, FADE, FPS, MUSIC, MUSIC_JOIN, SHOTS, Sfx, VOICE, sec,
} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const easeOut = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

const CardContent: React.FC<{card: Card}> = ({card}) => {
  const f = useCurrentFrame();
  const p = easeOut(f, 0, 14);
  const rise = (1 - p) * 10;

  if (card.kind === 'chapter') {
    return <AbsoluteFill style={{
      justifyContent: 'center', alignItems: 'center', fontFamily,
      color: C.text, textAlign: 'center',
    }}>
      <div style={{
        fontSize: 110, lineHeight: 1.06, fontWeight: 600, letterSpacing: '-0.045em',
        opacity: p, transform: `translateY(${rise}px)`,
      }}>
        {(card.parts ?? []).map((part, i) =>
          <span key={i} style={{color: part.hot ? C.hot : C.text}}>{part.text}</span>)}
      </div>
    </AbsoluteFill>;
  }

  return <AbsoluteFill style={{
    justifyContent: 'center', alignItems: 'center', fontFamily,
    color: C.text, textAlign: 'center',
  }}>
    <div style={{opacity: p, transform: `translateY(${rise}px)`, display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
      {card.lead ? <div style={{
        fontSize: 17, fontWeight: 500, letterSpacing: '.18em', textTransform: 'uppercase',
        color: C.muted, marginBottom: 20,
      }}>{card.lead}</div> : null}
      <div style={{
        fontSize: card.kind === 'close' ? 176 : 172, lineHeight: 1,
        fontWeight: 600, letterSpacing: '-0.055em',
      }}>{card.title}</div>
      {card.sub ? <div style={{
        marginTop: 30, fontSize: 24, fontWeight: 400, letterSpacing: '-0.01em',
        color: C.muted,
      }}>{card.sub}</div> : null}
    </div>
  </AbsoluteFill>;
};

const CardLayer: React.FC<{card: Card}> = ({card}) => {
  const total = sec(card.end - card.start);
  return <Sequence from={sec(card.start) - FADE / 2} durationInFrames={total + FADE}>
    <Fade total={total} background="#000">
      <Sequence from={FADE / 2} durationInFrames={total + FADE / 2}>
        <CardContent card={card} />
      </Sequence>
    </Fade>
  </Sequence>;
};

const DiagramLayer: React.FC = () => {
  const total = sec(103 - 67.5);
  return <Sequence from={sec(67.5) - FADE / 2} durationInFrames={total + FADE}>
    <Fade total={total} background="#000">
      <Sequence from={FADE / 2} durationInFrames={total + FADE / 2}>
        <Diagram />
      </Sequence>
    </Fade>
  </Sequence>;
};

const musicLevel = (t: number) => {
  let duck = 0;
  const ramp = 0.4;
  for (const v of VOICE) {
    const start = v.at;
    const end = v.at + (v.to - v.from);
    const d = t < start
      ? clamp((t - (start - ramp)) / ramp)
      : t > end
        ? 1 - clamp((t - end) / ramp)
        : 1;
    duck = Math.max(duck, d);
  }
  const level = 0.316 + (0.1 - 0.316) * duck;
  const fadeIn = clamp(t / 0.6);
  const fadeOut = clamp((DURATION_S - t) / 0.4);
  return level * fadeIn * fadeOut;
};

const HALF = MUSIC.xfade / 2;
// Equal-power crossfade across the join.
const xfade = (t: number, incoming: boolean) => {
  const x = clamp((t - (MUSIC_JOIN - HALF)) / MUSIC.xfade);
  return incoming ? Math.sin((x * Math.PI) / 2) : Math.cos((x * Math.PI) / 2);
};

const MUSIC_SRC = staticFile('walkthrough/audio/solvikmusic-chill-technology-604083.mp3');

const Music: React.FC = () => {
  const aFrames = sec(MUSIC_JOIN + HALF);
  const bStart = sec(MUSIC_JOIN - HALF);
  return <>
    <Sequence durationInFrames={aFrames}>
      <Audio src={MUSIC_SRC} trimBefore={sec(MUSIC.offset)}
        volume={(f) => musicLevel(f / FPS) * xfade(f / FPS, false)} />
    </Sequence>
    <Sequence from={bStart}>
      <Audio src={MUSIC_SRC} trimBefore={sec(MUSIC.cutTo - HALF)}
        volume={(f) => {
          const t = (f + bStart) / FPS;
          return musicLevel(t) * xfade(t, true);
        }} />
    </Sequence>
  </>;
};

const TAP = 'walkthrough/sfx/tap.mp3';
const sfxList = (): Sfx[] => {
  const list: Sfx[] = [];
  for (const shot of SHOTS) {
    for (const r of shot.rings ?? []) {
      const local = sourceTimeToLocal(shot, r.sourceTime);
      if (local === null) throw new Error(`ring outside shot ${shot.id}`);
      const at = shot.start + local / FPS;
      list.push(shot.id === 's10-mute'
        ? {at, src: 'sfx/mute_chime.wav', volume: 0.35}
        : {at, src: TAP, volume: 0.45});
    }
  }
  for (const c of CARDS) {
    if (c.kind === 'chapter' && c.id !== 'open') {
      list.push({at: c.start - 0.25, src: 'walkthrough/sfx/whoosh.mp3', volume: 0.3});
    }
  }
  const wordmark = CARDS.find((c) => c.kind === 'wordmark');
  if (wordmark) list.push({at: wordmark.start, src: 'sfx/impact.mp3', volume: 0.5});
  const brain = SHOTS.find((s) => s.id === 's8-brain');
  if (brain) list.push({at: brain.start - 1.6, src: 'walkthrough/sfx/swell.mp3', volume: 0.25});
  for (const at of DIAGRAM_POPS) list.push({at, src: 'walkthrough/sfx/pop.mp3', volume: 0.3});
  const close = CARDS.find((c) => c.kind === 'close');
  if (close) list.push({at: close.start, src: 'walkthrough/sfx/resolve.mp3', volume: 0.4});
  return list;
};
const SFX = sfxList();

export const Walkthrough: React.FC = () => {
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, #0A0B0D 0%, #000 72%)', color: C.text, fontFamily}}>
    <Music />
    {SFX.map((x, i) => <Sequence key={`sfx${i}`} from={sec(x.at)}>
      <Audio src={staticFile(x.src)} volume={x.volume} />
    </Sequence>)}
    {VOICE.map((v) => {
      const frames = sec(v.to - v.from);
      return <Sequence key={v.line} from={sec(v.at)} durationInFrames={frames}>
        <Audio
          src={staticFile('walkthrough/audio/voice_david.mp3')}
          trimBefore={sec(v.from)}
          durationInFrames={frames}
          volume={1}
        />
      </Sequence>;
    })}

    {SHOTS.map((shot) => <ShotLayer key={shot.id} shot={shot} />)}
    <DiagramLayer />
    {CARDS.map((card) => <CardLayer key={card.id} card={card} />)}
  </AbsoluteFill>;
};
