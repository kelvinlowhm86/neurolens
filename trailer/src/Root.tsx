import React from 'react';
import {Composition} from 'remotion';
import {Trailer} from './Trailer';
import {DURATION, FPS} from './beats';
export const Root: React.FC = () => <Composition id="NeuroLensTrailer" component={Trailer} durationInFrames={DURATION} fps={FPS} width={1920} height={1080} />;
