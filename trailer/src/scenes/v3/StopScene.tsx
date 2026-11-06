import React from 'react';
import {AbsoluteFill} from 'remotion';
import {CUE} from '../../script';
import {moments} from '../../beats';
import {Caption} from '../../edit/ui';

export const StopScene: React.FC = () =>
  <AbsoluteFill style={{background: '#000'}}>
    <Caption cue={CUE.stopB} offset={moments.stop} size={148} y={620} maxWidth={1520} outFrames={1} loosen={0} />
  </AbsoluteFill>;
