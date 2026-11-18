import React from 'react';
import {AbsoluteFill, Sequence, Series, useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../edit/ui';
import {BG, CreditStrip, easeOut} from './common';
import {Intro} from './intro/Intro';
import {DURATION as INTRO} from './intro/timeline';
import {WalkthroughV6Diagram, DURATION as DIAGRAM, creditWidgetOpacity} from './Diagram37';
import {Result} from './result/Result';
import {DURATION as RESULT} from './result/timeline';
import {Outro} from './outro/Outro';
import {DURATION as OUTRO} from './outro/timeline';

// Intro, Diagram, Result and Outro back to back. The cuts are short dips through the page colour,
// except Intro -> Diagram: the intro ends on the "your credit" strip (held) and the diagram brings
// its own strip in a little later, so the strip is carried across and never blinks off.
const DIP = 8; // frames per side of a dip

export const DURATION = INTRO + DIAGRAM + RESULT + OUTRO;

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
      <CreditStrip opacity={1 - creditWidgetOpacity(frame)} held={1} status="held" lit />
    </div>
  );
};

export const WalkthroughV6Full: React.FC = () => (
  <AbsoluteFill style={{background: BG}}>
    <Series>
      <Series.Sequence durationInFrames={INTRO}><Intro /></Series.Sequence>
      <Series.Sequence durationInFrames={DIAGRAM}>
        <Part length={DIAGRAM} dipOut><WalkthroughV6Diagram /></Part>
        <Sequence durationInFrames={DIAGRAM_STRIP_IN} layout="none"><CarriedStrip /></Sequence>
      </Series.Sequence>
      <Series.Sequence durationInFrames={RESULT}><Part length={RESULT} dipOut><Result /></Part></Series.Sequence>
      <Series.Sequence durationInFrames={OUTRO}><Part length={OUTRO} dipIn><Outro /></Part></Series.Sequence>
    </Series>
  </AbsoluteFill>
);
