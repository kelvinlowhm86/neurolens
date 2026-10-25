import {Easing, interpolate, spring} from 'remotion';
import {FPS, beats} from '../beats';

const sorted = [...beats].map((b) => ({frame: b.frame, strength: b.strength ?? 1})).sort((a, b) => a.frame - b.frame);

export const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
export const ease = (f: number, a: number, b: number) =>
  interpolate(f, [a, b], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

export const enter = (frame: number, delay = 0, damping = 18) =>
  spring({frame: Math.max(0, frame - delay), fps: FPS, config: {damping, stiffness: 170, mass: 0.72}});

export const lastBeat = (frame: number) => {
  let found: (typeof sorted)[number] | undefined;
  for (const beat of sorted) if (beat.frame <= frame) found = beat;
  return found;
};

export const beatPulse = (frame: number, decay = 5.5) => {
  const beat = lastBeat(frame);
  if (!beat) return 0;
  return Math.exp(-Math.max(0, frame - beat.frame) / decay) * Math.min(1.15, beat.strength ?? 1);
};

