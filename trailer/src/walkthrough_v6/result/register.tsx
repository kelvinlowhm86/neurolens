import {Composition} from 'remotion';
import {FPS, HEIGHT, WIDTH, DURATION} from './timeline';
import {Result} from './Result';

export const ResultComposition = (
  <Composition
    id="WalkthroughV6Result"
    component={Result}
    durationInFrames={DURATION}
    fps={FPS}
    width={WIDTH}
    height={HEIGHT}
  />
);
