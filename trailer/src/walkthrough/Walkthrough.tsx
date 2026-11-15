import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {Built} from './Built';
import {Close} from './Close';
import {Journey} from './Journey';
import {Opening} from './Opening';
import {Result} from './Result';
import {SceneLayer} from './Scene';
import {Upload, UploadCaption} from './Upload';
import {
  DURATION_S, FPS, LINES, MUSIC_OFFSET, SCENES, SFX_CUES, VOICE_SOURCE, sec,
} from './timeline';

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

const musicLevel = (t: number) => {
  let duck = 0;
  const ramp = 0.42;
  for (const line of LINES) {
    const d = t < line.start
      ? clamp((t - (line.start - ramp)) / ramp)
      : t > line.end
        ? 1 - clamp((t - line.end) / ramp)
        : 1;
    duck = Math.max(duck, d);
  }
  const bed = 0.23;
  const under = 0.095;
  const level = bed + (under - bed) * duck;
  const fadeIn = clamp(t / 0.7);
  const fadeOut = clamp((DURATION_S - t) / 0.45);
  return level * fadeIn * fadeOut;
};

const MUSIC_SRC = staticFile('walkthrough/audio/solvikmusic-chill-technology-604083.mp3');
const VOICE_SRC = staticFile(`walkthrough/${VOICE_SOURCE}`);

// The track is 144.72 s; MUSIC_OFFSET is chosen so its own ending lands on the final frame.
const Music: React.FC = () => (
  <Audio
    src={MUSIC_SRC}
    trimBefore={sec(MUSIC_OFFSET)}
    volume={(f) => musicLevel(f / FPS)}
  />
);

export const Walkthrough: React.FC = () => {
  return <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, #0A0B0D 0%, #000 72%)', color: C.text, fontFamily}}>
    <Music />
    <Audio src={VOICE_SRC} volume={1} />
    {SFX_CUES.map((cue, i) => <Sequence key={`sfx-${i}`} from={sec(cue.at)}>
      <Audio src={staticFile(cue.src)} volume={cue.volume} />
    </Sequence>)}

    <SceneLayer start={SCENES.opening.start} end={SCENES.opening.end}><Opening /></SceneLayer>
    <SceneLayer start={SCENES.upload.start} end={SCENES.upload.end}><Upload /><UploadCaption /></SceneLayer>
    <SceneLayer start={SCENES.journey.start} end={SCENES.journey.end}><Journey /></SceneLayer>
    <SceneLayer start={SCENES.result.start} end={SCENES.result.end}><Result /></SceneLayer>
    <SceneLayer start={SCENES.built.start} end={SCENES.built.end}><Built /></SceneLayer>
    <SceneLayer start={SCENES.close.start} end={SCENES.close.end}><Close /></SceneLayer>
  </AbsoluteFill>;
};
