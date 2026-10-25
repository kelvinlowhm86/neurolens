import React from 'react';
import {Composition} from 'remotion';
import {Trailer} from './Trailer';
import {DURATION, FPS} from './beats';
import {Scanner} from './scenes/Scanner';
import {BrainHero} from './scenes/BrainHero';
export const Root: React.FC = () => <>
  <Composition id="NeuroLensTrailer" component={Trailer} durationInFrames={DURATION} fps={FPS} width={1920} height={1080} />
  <Composition id="Scanner" component={Scanner} durationInFrames={300} fps={FPS} width={1920} height={1080} />
  <Composition id="BrainHero" component={BrainHero} durationInFrames={360} fps={FPS} width={1920} height={1080} />
</>;
