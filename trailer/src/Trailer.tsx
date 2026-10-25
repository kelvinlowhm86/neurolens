import React from 'react';
import {AbsoluteFill, Audio, Sequence, interpolate, staticFile} from 'remotion';
import {cuts, f, moments, MUSIC_START, SFX} from './beats';
import {CUE} from './script';
import {Shatter} from './edit/Shatter';
import {C, PulseStage, fontFamily} from './edit/ui';
import {OpenScene} from './scenes/v3/OpenScene';
import {ScannerScene} from './scenes/v3/ScannerScene';
import {StopScene} from './scenes/v3/StopScene';
import {DropScene} from './scenes/v3/DropScene';
import {HookedScene, DriftScene} from './scenes/v3/ClipChartScene';
import {SignalsScene} from './scenes/v3/SignalsScene';
import {GroupsScene} from './scenes/v3/GroupsScene';
import {MuteScene} from './scenes/v3/MuteScene';
import {CompareScene} from './scenes/v3/CompareScene';
import {ModelScene} from './scenes/v3/ModelScene';
import {WeeksAgainScene} from './scenes/v3/WeeksAgainScene';
import {MinutesScene} from './scenes/v3/MinutesScene';
import {TitleScene} from './scenes/v3/TitleScene';
import {CreditsScene} from './scenes/v3/CreditsScene';

const span = (a: number, b: number) => Math.max(1, b - a);
const MASTER = 0.891;

const musicLevel = (frame: number) => {
  const down = interpolate(frame, [cuts.mute, cuts.mute + f(.15)], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const up = interpolate(frame, [moments.muteStop, moments.muteStop + f(.15)], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (frame <= cuts.mute) return 1;
  if (frame < cuts.mute + f(.15)) return down;
  if (frame < moments.muteStop) return 0;
  return up;
};

const titleAssembleFrom = moments.finalHit - f(.65);
const impactFrom = moments.drop - f(SFX.impact.HIT_OFFSET);
const impactDuration = moments.drop + f(3) - impactFrom;
const impactFade = f(1.5);
const muteFrom = cuts.mute - f(SFX.mute.HIT_OFFSET);
const scribbleFrom = moments.weeksStop - f(SFX.scribble.HIT_OFFSET);

export const Trailer: React.FC = () => {
  return <AbsoluteFill style={{background: '#050607', color: C.text, fontFamily}}>
    <Audio src={staticFile('music3.wav')} trimBefore={f(MUSIC_START)} volume={(t) => .8 * musicLevel(t) * MASTER} />
    <Audio src={staticFile('music3-muffled.wav')} trimBefore={f(MUSIC_START)} volume={(t) => .8 * (1 - musicLevel(t)) * MASTER} />
    <Sequence from={impactFrom} durationInFrames={impactDuration}>
      <Audio src={staticFile(SFX.impact.file)} volume={(t) => interpolate(t, [impactDuration - impactFade, impactDuration], [.45, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) * MASTER} />
    </Sequence>
    <Sequence from={muteFrom}>
      <Audio src={staticFile(SFX.mute.file)} volume={.5 * MASTER} />
    </Sequence>
    <Sequence from={scribbleFrom}>
      <Audio src={staticFile(SFX.scribble.file)} volume={.5 * MASTER} />
    </Sequence>
    <PulseStage>
      <Sequence from={cuts.open} durationInFrames={span(cuts.open, cuts.scanner)}><OpenScene /></Sequence>
      <Sequence from={cuts.scanner} durationInFrames={span(cuts.scanner, moments.stop)}><ScannerScene /></Sequence>
      <Sequence from={moments.stop} durationInFrames={span(moments.stop, cuts.drop)}><StopScene /></Sequence>
      <Sequence from={cuts.drop} durationInFrames={span(cuts.drop, cuts.hooked)}><DropScene /></Sequence>
      <Sequence from={cuts.drop} durationInFrames={f(.8)}>
        <Shatter text={CUE.stopB.text} at={cuts.drop} offset={cuts.drop} size={148} y={620} weight={600} />
      </Sequence>
      <Sequence from={cuts.hooked} durationInFrames={span(cuts.hooked, cuts.drift)}><HookedScene /></Sequence>
      <Sequence from={cuts.drift} durationInFrames={span(cuts.drift, cuts.signals)}><DriftScene /></Sequence>
      <Sequence from={cuts.signals} durationInFrames={span(cuts.signals, cuts.groups)}><SignalsScene /></Sequence>
      <Sequence from={cuts.groups} durationInFrames={span(cuts.groups, cuts.mute)}><GroupsScene /></Sequence>
      <Sequence from={cuts.mute} durationInFrames={span(cuts.mute, cuts.compare)}><MuteScene /></Sequence>
      <Sequence from={cuts.compare} durationInFrames={span(cuts.compare, cuts.model)}><CompareScene /></Sequence>
      <Sequence from={cuts.model} durationInFrames={span(cuts.model, cuts.weeksAgain)}><ModelScene /></Sequence>
      <Sequence from={cuts.weeksAgain} durationInFrames={span(cuts.weeksAgain, cuts.minutes)}><WeeksAgainScene /></Sequence>
      <Sequence from={cuts.minutes} durationInFrames={span(cuts.minutes, moments.silence)}><MinutesScene /></Sequence>
      <Sequence from={titleAssembleFrom} durationInFrames={moments.finalHit - titleAssembleFrom + 1}>
        <Shatter text={CUE.endingTitle.text} at={moments.finalHit} offset={titleAssembleFrom} mode="assemble"
          size={238} y={430} weight={600} letterSpacing={-.07} lineHeight={1}
          textShadow="0 24px 90px rgba(0,0,0,.8), 0 0 90px rgba(255,138,42,.16)" />
      </Sequence>
      <Sequence from={cuts.title} durationInFrames={span(cuts.title, cuts.credits)}><TitleScene /></Sequence>
      <Sequence from={cuts.credits} durationInFrames={span(cuts.credits, cuts.end)}><CreditsScene /></Sequence>
    </PulseStage>
  </AbsoluteFill>;
};
