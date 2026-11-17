import React from 'react';
import {Composition} from 'remotion';
import {Trailer} from './Trailer';
import {DURATION, FPS} from './beats';
import {Scanner} from './scenes/Scanner';
import {BrainHero} from './scenes/BrainHero';
import {Walkthrough} from './walkthrough/Walkthrough';
import {DURATION as WALKTHROUGH_DURATION} from './walkthrough/timeline';
import {WalkthroughSeq4Pilot} from './walkthrough_v5/WalkthroughSeq4Pilot';
import {DURATION as SEQ4_DURATION} from './walkthrough_v5/timing';
import {Pilot34} from './walkthrough_v6/Pilot34';
import {DURATION as V6_PILOT34_DURATION} from './walkthrough_v6/Pilot34';
import {StyleA_Glass} from './walkthrough_v6/styles/StyleA_Glass';
import {StyleB_Icons} from './walkthrough_v6/styles/StyleB_Icons';
import {StyleC_Blueprint} from './walkthrough_v6/styles/StyleC_Blueprint';
import {StyleD_Spatial} from './walkthrough_v6/styles/StyleD_Spatial';
import {DURATION as STYLE_DURATION} from './walkthrough_v6/styles/StyleA_Glass';
export const Root: React.FC = () => <>
  <Composition id="NeuroLensTrailer" component={Trailer} durationInFrames={DURATION} fps={FPS} width={1920} height={1080} />
  <Composition id="Scanner" component={Scanner} durationInFrames={300} fps={FPS} width={1920} height={1080} />
  <Composition id="BrainHero" component={BrainHero} durationInFrames={360} fps={FPS} width={1920} height={1080} />
  <Composition id="Walkthrough" component={Walkthrough} durationInFrames={WALKTHROUGH_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="WalkthroughSeq4Pilot" component={WalkthroughSeq4Pilot} durationInFrames={SEQ4_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="WalkthroughV6Pilot34" component={Pilot34} durationInFrames={V6_PILOT34_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="StyleA-Glass" component={StyleA_Glass} durationInFrames={STYLE_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="StyleB-Icons" component={StyleB_Icons} durationInFrames={STYLE_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="StyleC-Blueprint" component={StyleC_Blueprint} durationInFrames={STYLE_DURATION} fps={30} width={1920} height={1080} />
  <Composition id="StyleD-Spatial" component={StyleD_Spatial} durationInFrames={STYLE_DURATION} fps={30} width={1920} height={1080} />
</>;
