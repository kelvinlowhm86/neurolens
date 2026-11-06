import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import bunny from '../../bunny.json';
import sintel from '../../sintel.json';
import type {Cue} from '../../script';
import {CUE, LABEL} from '../../script';
import {CLIPS, FPS, cuts} from '../../beats';
import {Graph, signed} from '../../edit/Graph';
import {C, Caption, FullBleed, Shade} from '../../edit/ui';
import {clamp, ease} from '../../edit/anim';

type Props = {
  cue: Cue;
  clip: 'bunny' | 'sintel';
  values: number[];
  sceneStart: number;
  windowStart: number;
  windowEnd: number;
  color: string;
  label: string;
  mode: 'peak' | 'low';
};

const rangeOf = (values: number[], from: number, to: number): [number, number] => {
  const slice = values.slice(Math.floor(from), Math.floor(to) + 1);
  const low = Math.min(0, ...slice), high = Math.max(0, ...slice);
  const pad = (high - low) * .18 || .04;
  return [low - pad, high + pad];
};

export const ClipChartScene: React.FC<Props> = ({
  cue, clip, values, sceneStart, windowStart, windowEnd, color, label, mode,
}) => {
  const frame = useCurrentFrame();
  const g = frame + sceneStart;
  const t = windowStart + (g - sceneStart) / FPS;
  const progress = clamp((t - windowStart) / (windowEnd - windowStart));
  const slice = values.slice(Math.floor(windowStart), Math.floor(windowEnd) + 1);
  const markerValue = mode === 'peak' ? Math.max(...slice) : Math.min(...slice);
  const markerSecond = slice.indexOf(markerValue) + Math.floor(windowStart);
  const reach = sceneStart + (markerSecond - windowStart) * FPS;
  const pop = ease(g, reach, reach + 8);
  const markerLabel = `${signed(markerValue)} ${mode === 'peak' ? LABEL.peak : LABEL.trough}`;
  return <AbsoluteFill style={{background: C.bg}}>
    <FullBleed clip={clip} start={windowStart} />
    <Shade top="rgba(6,7,9,.56)" bottom="rgba(6,7,9,.96)" />
    <Caption cue={cue} offset={sceneStart} size={121} y={250} maxWidth={1720} />
    <div style={{position: 'absolute', left: 96, right: 96, bottom: 48}}>
      <Graph
        values={values}
        from={windowStart}
        to={windowEnd}
        range={rangeOf(values, windowStart, windowEnd)}
        width={1728}
        height={360}
        progress={progress}
        playhead={t}
        marker={{second: markerSecond, label: markerLabel, size: 44, pop}}
        zeroLabel={LABEL.typical}
        color={color}
        label={label}
        background
      />
    </div>
  </AbsoluteFill>;
};

export const HookedScene: React.FC = () =>
  <ClipChartScene cue={CUE.hooked} clip="bunny" values={bunny.signals.overall}
    sceneStart={cuts.hooked} windowStart={CLIPS.hooked[0]} windowEnd={CLIPS.hooked[1]} color={C.hot} label="hooked" mode="peak" />;

export const DriftScene: React.FC = () =>
  <ClipChartScene cue={CUE.drift} clip="bunny" values={bunny.signals.overall}
    sceneStart={cuts.drift} windowStart={CLIPS.drift[0]} windowEnd={CLIPS.drift[1]} color={C.cool} label="drift" mode="low" />;
