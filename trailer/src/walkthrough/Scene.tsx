import React from 'react';
import {Sequence} from 'remotion';
import {Fade} from './Panel';
import {FADE, sec} from './timeline';

export const SceneLayer: React.FC<{start: number; end: number; children: React.ReactNode}> = ({start, end, children}) => {
  const total = sec(end - start);
  return <Sequence from={sec(start) - FADE / 2} durationInFrames={total + FADE}>
    <Fade total={total} background="#000">
      <Sequence from={FADE / 2} durationInFrames={total + FADE / 2}>
        {children}
      </Sequence>
    </Fade>
  </Sequence>;
};
