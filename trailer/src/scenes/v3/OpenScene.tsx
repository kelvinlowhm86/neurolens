import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame} from 'remotion';
import {CUE} from '../../script';
import {cuts, moments} from '../../beats';
import {C, Caption, FullBleed, Shade} from '../../edit/ui';
import {beatPulse, clamp} from '../../edit/anim';

const span = (a: number, b: number) => Math.max(1, b - a);

export const OpenScene: React.FC = () => {
  const f = useCurrentFrame();
  const g = f + cuts.open;
  const push = clamp((g - cuts.open) / span(cuts.open, moments.stop));
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: C.bg}}>
    <Sequence durationInFrames={span(cuts.open, cuts.sintelOpen)}>
      <AbsoluteFill style={{transform: `scale(${1.04 + push * .1 + beat * .003})`}}><FullBleed clip="bunny" start={20} /></AbsoluteFill>
    </Sequence>
    <Sequence from={cuts.sintelOpen - cuts.open} durationInFrames={span(cuts.sintelOpen, cuts.scanner)}>
      <AbsoluteFill style={{transform: `scale(${1.04 + push * .1 + beat * .003})`}}><FullBleed clip="sintel" start={14} /></AbsoluteFill>
    </Sequence>
    <Shade top="rgba(7,8,10,.24)" bottom="rgba(7,8,10,.78)" />
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 53%, transparent 28%, rgba(5,6,8,.42) 100%)'}} />
    <Caption cue={CUE.openA} offset={cuts.open} size={103} y={505} maxWidth={1450} />
    <Caption cue={CUE.openB} offset={cuts.open} size={103} y={505} maxWidth={1450} />
  </AbsoluteFill>;
};
