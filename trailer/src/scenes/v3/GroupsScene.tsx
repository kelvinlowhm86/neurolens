import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import bunny from '../../bunny.json';
import {LABEL} from '../../script';
import {cuts, f, moments} from '../../beats';
import {Graph, at} from '../../edit/Graph';
import {ease} from '../../edit/anim';
import {C} from '../../edit/ui';
import {COLUMN_POS, GROUPS, GroupTitle, SignalField, SignalLabel} from './signals';

const rangeOf = (values: number[], from: number, to: number): [number, number] => {
  const slice = values.slice(from, to + 1);
  const low = Math.min(0, ...slice), high = Math.max(0, ...slice);
  const pad = (high - low) * .18 || .04;
  return [low - pad, high + pad];
};

const chartTop = 640;
const chartWidth = 480;
const chartHeight = 200;

export const GroupsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.groups;
  const draw = ease(g, cuts.groups, moments.breakSmall);
  const out = 1 - ease(g, moments.breakSmall, moments.breakSmall + f(.35));
  return <AbsoluteFill style={{background: C.bg}}>
    <SignalField />
    <AbsoluteFill style={{opacity: out, transform: `scale(${.96 + .04 * out})`}}>
      {GROUPS.map((group) => <div key={group.title}>
        <GroupTitle title={group.title} x={group.x} color={group.color} />
        {group.keys.map((key) => {
          const i = LABEL.signals.findIndex((name) => name.toLowerCase() === key);
          const values = bunny.signals[key as keyof typeof bunny.signals] as number[];
          return <SignalLabel
            key={key}
            name={LABEL.signals[i]}
            value={at(values, 25)}
            x={group.x}
            y={COLUMN_POS[i][1]}
            nameSize={42}
            valueSize={40}
          />;
        })}
        <div style={{position: 'absolute', left: group.x - chartWidth / 2, top: chartTop}}>
          <Graph
            values={bunny.signals[group.series] as number[]}
            from={20}
            to={32}
            range={rangeOf(bunny.signals[group.series] as number[], 20, 32)}
            width={chartWidth}
            height={chartHeight}
            progress={draw}
            color={group.color}
            label={`group-${group.title}`}
            background
          />
        </div>
      </div>)}
    </AbsoluteFill>
  </AbsoluteFill>;
};
