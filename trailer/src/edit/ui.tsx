import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile, useCurrentFrame} from 'remotion';
import {getInfo} from '@remotion/google-fonts/Geist';
import {loadFontFromInfo} from '@remotion/google-fonts/from-info';
import type {Cue} from '../script';
import {FPS} from '../beats';
import {beatPulse, clamp, ease, enter} from './anim';

const info = getInfo();
const localInfo = {...info, fonts: {normal: Object.fromEntries(
  ['400', '500', '600'].map((w) => [w, {latin: staticFile(`geist-${w}.woff2`)}]),
)}, subsets: ['latin'], variable: undefined};
const {fontFamily} = loadFontFromInfo(localInfo, 'normal', {weights: ['400', '500', '600'], subsets: ['latin']});

export {fontFamily};

export const C = {
  bg: '#0E0F11', text: '#ECEDEF', muted: '#A0A6AF', rule: '#24262B',
  hot: '#FF8A2A', cool: '#6F7E99', surface: '#17181B',
};

export const Caption: React.FC<{
  cue: Cue; offset: number; size: number; y?: number; maxWidth?: number;
  color?: string; weight?: number; letterSpacing?: number; loosen?: number; outFrames?: number;
}> = ({cue, offset, size, y = 540, maxWidth = 1560, color = C.text, weight = 600, letterSpacing = -.055, loosen = 2, outFrames = 5}) => {
  const f = useCurrentFrame();
  const g = f + offset;
  const words = cue.text.split(' ');
  const out = 1 - ease(g, cue.end - outFrames, cue.end);
  const beat = beatPulse(g) * clamp((g - cue.start) / 3);
  return <div style={{position: 'absolute', left: 0, right: 0, top: y, transform: 'translateY(-50%)', display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
    <div style={{width: maxWidth, textAlign: 'center', fontSize: size, lineHeight: 1.03, fontWeight: weight, letterSpacing: `${letterSpacing}em`, color, textShadow: '0 8px 42px rgba(0,0,0,.62)', fontVariantNumeric: 'tabular-nums'}}>
      {words.map((word, i) => {
        const delay = cue.start + i * loosen;
        const p = enter(g, delay, 19);
        const shown = clamp((g - delay) / 4);
        const breath = 1 + Math.sin((g - cue.start + i * 3) / 21) * .006;
        return <React.Fragment key={`${word}-${i}`}>
          {i > 0 ? ' ' : ''}
          <span style={{display: 'inline-block', opacity: shown * out, transform: `translateY(${(1 - p) * 42}px) scale(${(.94 + p * .06) * breath + beat * .012})`, filter: `blur(${(1 - clamp(p)) * 11}px)`}}>{word}</span>
        </React.Fragment>;
      })}
    </div>
  </div>;
};

export const Flash: React.FC<{at: number; offset?: number; length?: number; strength?: number; color?: string}> = ({
  at: hit, offset = 0, length = 8, strength = .38, color = '#FFFFFF',
}) => {
  const g = useCurrentFrame() + offset;
  const amount = Math.max(0, 1 - (g - hit) / length) * (g >= hit - 1 ? 1 : 0);
  return <AbsoluteFill style={{background: color, opacity: amount * amount * strength, pointerEvents: 'none', mixBlendMode: 'screen'}} />;
};

export const Shade: React.FC<{top?: string; bottom?: string}> = ({top = 'transparent', bottom = 'rgba(7,8,10,.82)'}) =>
  <AbsoluteFill style={{background: `linear-gradient(180deg, ${top} 0%, transparent 32%, transparent 54%, ${bottom} 100%)`, pointerEvents: 'none'}} />;

export const FullBleed: React.FC<{clip: 'bunny' | 'sintel'; start: number; blur?: number}> = ({clip, start, blur = 0}) => {
  return <AbsoluteFill style={{overflow: 'hidden', background: C.bg}}>
    <OffthreadVideo
      src={staticFile(clip === 'bunny' ? 'bbb_720p_55s.mp4' : 'sintel_trailer_720p.mp4')}
      trimBefore={Math.round(start * FPS)}
      muted
      style={{width: '100%', height: '100%', objectFit: 'cover', filter: `blur(${blur}px) saturate(1.03)`}} />
    <AbsoluteFill style={{background: 'linear-gradient(110deg, rgba(6,7,9,.56), transparent 46%, rgba(6,7,9,.2))'}} />
  </AbsoluteFill>;
};
