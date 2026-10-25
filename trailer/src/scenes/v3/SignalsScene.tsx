import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import bunny from '../../bunny.json';
import {CUE, LABEL} from '../../script';
import {cuts, f} from '../../beats';
import {at} from '../../edit/Graph';
import {beatPulse, clamp, ease, enter} from '../../edit/anim';
import {C} from '../../edit/ui';
import {COLUMN_POS, GROUPS, GroupTitle, RING, SignalField, SignalLabel} from './signals';

const arrays = LABEL.signals.map((name) => bunny.signals[name.toLowerCase() as keyof typeof bunny.signals] as number[]);
const flyStart = cuts.groups - f(.5);

export const SignalsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const g = frame + cuts.signals;
  const fly = ease(g, flyStart, flyStart + 8);
  const ringFade = 1 - fly;
  const beat = beatPulse(g);
  return <AbsoluteFill style={{background: C.bg}}>
    <SignalField />
    <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, opacity: .2 * ringFade}}>
      {RING.map(([x, y], i) => <line key={i} x1={960} y1={540} x2={x} y2={y} stroke={C.cool} strokeWidth="1" strokeDasharray="2 9" />)}
      <circle cx={960} cy={540} r={220 + beat * 8} fill="none" stroke={C.rule} strokeWidth="1" />
      <circle cx={960} cy={540} r={390 + beat * 12} fill="none" stroke={C.rule} strokeWidth="1" strokeOpacity=".7" />
    </svg>
    {CUE.signalNames.map((cue, i) => {
      const [rx, ry] = RING[i], [cx, cy] = COLUMN_POS[i];
      const p = enter(g, cue.start, 15);
      const pop = .62 + p * .38 + beat * .04;
      return <SignalLabel
        key={cue.id}
        name={cue.text}
        value={at(arrays[i], 25)}
        x={rx + (cx - rx) * fly}
        y={ry + (cy - ry) * fly}
        nameSize={72 + (42 - 72) * fly}
        valueSize={44 + (40 - 44) * fly}
        opacity={clamp((g - cue.start) / 4)}
        scale={pop + (1 - pop) * fly}
      />;
    })}
    {GROUPS.map((group) => <GroupTitle key={group.title} title={group.title} x={group.x} color={group.color} opacity={fly} />)}
  </AbsoluteFill>;
};
