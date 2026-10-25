import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {BrainHero} from '../BrainHero';
import {CUE} from '../../script';
import {cuts} from '../../beats';
import {beatPulse, ease} from '../../edit/anim';
import {C, Caption, Shade} from '../../edit/ui';

export const ModelScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.model;
  const push = ease(g, cuts.model, cuts.weeksAgain);
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{transform: `scale(${1.02 + push * .035 + beat * .003})`, transformOrigin: '50% 48%'}}>
      <BrainHero start={48} layout="solo" />
    </AbsoluteFill>
    <Shade top="rgba(6,7,9,.18)" bottom="rgba(6,7,9,.9)" />
    <Caption cue={CUE.model} offset={cuts.model} size={72} y={900} maxWidth={1700} />
  </AbsoluteFill>;
};
