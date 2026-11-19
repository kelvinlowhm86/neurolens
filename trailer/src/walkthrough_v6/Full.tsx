import React from 'react';
import {AbsoluteFill, Audio, Sequence, Series, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {BG, CreditStrip, easeOut} from './common';
import {Intro} from './intro/Intro';
import {DURATION as INTRO, LINES as INTRO_LINES, BROWSER_HOLD_SHARE} from './intro/timeline';
import {WalkthroughV6Diagram, DURATION as DIAGRAM, LINE_CUES as DIAGRAM_LINES, creditWidgetOpacity} from './Diagram37';
import {Result} from './result/Result';
import {DURATION as RESULT, LINES as RESULT_LINES} from './result/timeline';
import {Outro} from './outro/Outro';
import {DURATION as OUTRO, LINES as OUTRO_LINES} from './outro/timeline';
import {FPS, LineCue} from './voice';

// Intro, Diagram, Result and Outro back to back. The cuts are short dips through the page colour,
// except Intro -> Diagram: the intro ends on the "your credit" strip (held) and the diagram brings
// its own strip in a little later, so the strip is carried across and never blinks off.
const DIP = 8; // frames per side of a dip

export const DURATION = INTRO + DIAGRAM + RESULT + OUTRO;

// Voice: one wav per script line, placed at its line's first word. Music (bed file at -14 LUFS) is
// ducked: it sits ~15 dB under the voice (-19.9 LUFS) while someone speaks and rises in the pauses.
const MUSIC_UNDER_VOICE = 0.1; // -20 dB
const MUSIC_IN_GAPS = 0.22; // -13 dB
const DUCK_RAMP = 8; // frames to dip before a line and to recover after it
const MUSIC_FADE_IN = 2 * FPS;
const MUSIC_FADE_OUT = 4 * FPS;

// Every voice line as [start, end] frames in the full video.
const SPEECH: [number, number][] = (
  [[0, INTRO_LINES], [INTRO, DIAGRAM_LINES], [INTRO + DIAGRAM, RESULT_LINES], [INTRO + DIAGRAM + RESULT, OUTRO_LINES]] as [number, LineCue[]][]
).flatMap(([offset, lines]) => lines.map((l): [number, number] => [offset + l.start, offset + l.start + l.frames]));

// 1 while speaking, 0 in a pause, with short ramps either side of each line.
const speaking = (f: number) => Math.max(0, ...SPEECH.map(([s, e]) =>
  interpolate(f, [s - DUCK_RAMP, s, e, e + DUCK_RAMP], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})));

const VoiceLines: React.FC<{lines: LineCue[]}> = ({lines}) => (
  <>
    {lines.map((line) => (
      <Sequence key={line.id} from={line.start} durationInFrames={line.frames + 1} layout="none">
        <Audio src={staticFile(`walkthrough/v6voice/${line.id}.wav`)} />
      </Sequence>
    ))}
  </>
);

// bed.mp3 is the track followed by a crossfaded repeat of its calm section, longer than the video.
const MusicBed: React.FC = () => (
  <Audio
    src={staticFile('walkthrough/v6voice/bed.mp3')}
    volume={(f) => (MUSIC_IN_GAPS + (MUSIC_UNDER_VOICE - MUSIC_IN_GAPS) * speaking(f)) * interpolate(f, [0, MUSIC_FADE_IN, DURATION - MUSIC_FADE_OUT, DURATION], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}
  />
);

// Frames of the diagram before its own strip is fully in; the carried copy fades out as it fades in.
let DIAGRAM_STRIP_IN = 0;
while (DIAGRAM_STRIP_IN < DIAGRAM && creditWidgetOpacity(DIAGRAM_STRIP_IN) < 0.999) DIAGRAM_STRIP_IN += 1;

const Part: React.FC<{children: React.ReactNode; dipIn?: boolean; dipOut?: boolean; length: number}> = ({children, dipIn, dipOut, length}) => {
  const frame = useCurrentFrame();
  const inDip = dipIn ? 1 - easeOut(frame, 0, DIP) : 0;
  const outDip = dipOut ? easeOut(frame, length - DIP, length - 1) : 0;
  return (
    <AbsoluteFill>
      {children}
      <AbsoluteFill style={{background: BG, opacity: Math.max(inDip, outDip), pointerEvents: 'none', zIndex: 100}} />
    </AbsoluteFill>
  );
};

const CarriedStrip: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <div style={{position: 'absolute', inset: 0, fontFamily, color: C.text}}>
      <CreditStrip opacity={1 - creditWidgetOpacity(frame)} held={1} holdShare={BROWSER_HOLD_SHARE} status="held" lit />
    </div>
  );
};

export const WalkthroughV6Full: React.FC = () => (
  <AbsoluteFill style={{background: BG}}>
    <MusicBed />
    <Series>
      <Series.Sequence durationInFrames={INTRO}><Intro /><VoiceLines lines={INTRO_LINES} /></Series.Sequence>
      <Series.Sequence durationInFrames={DIAGRAM}>
        <Part length={DIAGRAM} dipOut><WalkthroughV6Diagram /></Part>
        <Sequence durationInFrames={DIAGRAM_STRIP_IN} layout="none"><CarriedStrip /></Sequence>
        <VoiceLines lines={DIAGRAM_LINES} />
      </Series.Sequence>
      <Series.Sequence durationInFrames={RESULT}><Part length={RESULT} dipOut><Result /></Part><VoiceLines lines={RESULT_LINES} /></Series.Sequence>
      <Series.Sequence durationInFrames={OUTRO}><Part length={OUTRO} dipIn><Outro /></Part><VoiceLines lines={OUTRO_LINES} /></Series.Sequence>
    </Series>
  </AbsoluteFill>
);
