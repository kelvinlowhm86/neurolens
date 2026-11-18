import React from 'react';
import {useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {Signpost, Stage, easeInOut, easeOut, lerp} from '../common';
import {DURATION, REPORT, ROW_IN, SHOT} from './timeline';

const fmt = (s: number) => `0:${String(s).padStart(2, '0')}`;
const range = `${fmt(REPORT.moment.start)}–${fmt(REPORT.moment.end)}`;

// Short gists lifted word for word from the real report text, shown dim: the voice says more than the screen.
const GIST = {onScreen: 'Black-and-white footage and old photos', brain: 'overall stays well above typical'};
for (const [key, source] of [[GIST.onScreen, REPORT.moment.what_happens], [GIST.brain, REPORT.moment.brain_response]]) {
  if (!source.includes(key)) throw new Error(`report gist "${key}" is not in the real report text`);
}

const CARD = {x: 160, y: 100, w: 1600, h: 740};

const Row: React.FC<{label: string; text: string; top: number; opacity: number; hot?: boolean; dim?: boolean; size?: number}> = ({
  label, text, top, opacity, hot, dim, size = 40,
}) => (
  <div style={{
    position: 'absolute', left: 60, right: 60, top, display: 'flex', gap: 40, alignItems: 'flex-start', opacity,
    transform: `translateY(${(1 - opacity) * 14}px)`,
  }}>
    <div style={{width: 250, flexShrink: 0, fontFamily, fontSize: 44, fontWeight: 600, letterSpacing: '-.01em', color: hot ? C.hot : C.muted, opacity: dim ? 0.8 : 1, paddingTop: 2}}>{label}</div>
    <div style={{flex: 1, fontFamily, fontSize: size, lineHeight: 1.3, fontWeight: 500, letterSpacing: '-.01em', color: hot ? C.text : C.muted, opacity: dim ? 0.7 : 1}}>{text}</div>
  </div>
);

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();

  // 9: the report card appears with "Then the report reads the whole thing" (9a), the three rows of one
  // moment follow the voice (9b), then the Do line alone (9d). Only the Do line is written out in full.
  const cardIn = easeOut(frame, SHOT['9a'].start + 2, SHOT['9a'].start + 20);
  const rowsIn = (n: number) => easeOut(frame, ROW_IN[n], ROW_IN[n] + 20);
  const rowsQuiet = easeInOut(frame, SHOT['9d'].start + 4, SHOT['9d'].start + 22);
  const cardOut = 1 - easeOut(frame, SHOT['10a'].start - 6, SHOT['10a'].start + 6);
  const bedrock = easeOut(frame, SHOT['9c'].start + 6, SHOT['9c'].start + 20) * (1 - easeOut(frame, SHOT['9d'].start, SHOT['9d'].start + 12));
  const push = 1 + 0.04 * easeInOut(frame, SHOT['9d'].start + 10, SHOT['9d'].end);

  // 10: two sentences.
  const first = easeOut(frame, SHOT['10a'].start + 4, SHOT['10a'].start + 22);
  const second = easeOut(frame, SHOT['10b'].start + 4, SHOT['10b'].start + 24);
  const firstDim = lerp(1, 0.38, easeInOut(frame, SHOT['10b'].start, SHOT['10b'].start + 16));
  const closingOut = 1 - easeOut(frame, SHOT['10b'].end - 8, SHOT['10b'].end + 4);

  // 11: the closing line, then black.
  const homeIn = easeOut(frame, SHOT['11'].start + 2, SHOT['11'].start + 22);
  const fadeBlack = easeInOut(frame, DURATION - 30, DURATION);

  return (
    <Stage>
      {cardOut > 0.01 ? (
        <div style={{opacity: cardIn * cardOut, position: 'absolute', inset: 0}}>
          <div style={{
            position: 'absolute', left: CARD.x, top: CARD.y, width: CARD.w, height: CARD.h, borderRadius: 28, boxSizing: 'border-box',
            background: `rgba(17,19,22,${0.92 * (1 - rowsQuiet)})`, border: `1.5px solid rgba(36,38,43,${1 - rowsQuiet})`,
            boxShadow: `0 30px 80px rgba(0,0,0,${0.55 * (1 - rowsQuiet)})`,
          }}>
            <div style={{position: 'absolute', left: 60, top: 56, fontFamily, fontSize: 44, fontWeight: 600, color: C.muted, opacity: 1 - rowsQuiet, fontVariantNumeric: 'tabular-nums'}}>{range}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, opacity: 1}}>
              <Row label="On screen" text={GIST.onScreen} top={170} dim opacity={rowsIn(0) * (1 - rowsQuiet)} />
              <Row label="Brain" text={GIST.brain} top={300} dim opacity={rowsIn(1) * (1 - rowsQuiet)} />
              <Row label="Do" text={REPORT.moment.suggestion ?? ''} hot top={440} opacity={rowsIn(2) * (1 - rowsQuiet)} />
            </div>
          </div>
          <Signpost text="Claude · Amazon Bedrock" y={CARD.y + CARD.h + 36} opacity={bedrock} />
          <div style={{
            position: 'absolute', left: 200, right: 200, top: 0, bottom: 0, display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', gap: 36, textAlign: 'center', opacity: rowsQuiet, transform: `scale(${push})`,
          }}>
            <div style={{fontFamily, fontSize: 56, fontWeight: 600, color: C.hot, letterSpacing: '-.01em'}}>Do</div>
            <div style={{fontFamily, fontSize: 72, lineHeight: 1.2, fontWeight: 600, letterSpacing: '-.025em', color: C.text}}>{REPORT.moment.suggestion}</div>
          </div>
        </div>
      ) : null}

      {first > 0.01 && closingOut > 0.01 ? (
        <div style={{
          position: 'absolute', left: 160, right: 160, top: 0, bottom: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 64, textAlign: 'center', fontFamily, color: C.text, opacity: closingOut,
        }}>
          <div style={{fontSize: 96, fontWeight: 600, letterSpacing: '-.03em', lineHeight: 1.1, opacity: first * firstDim, transform: `translateY(${(1 - second) * 102}px)`}}>None of this predicts your views.</div>
          <div style={{fontSize: 64, fontWeight: 500, letterSpacing: '-.02em', lineHeight: 1.25, color: '#D6DAE0', opacity: second, maxWidth: 1500}}>
            It shows how a brain is likely to respond, second by second, so you can fix things before you post.
          </div>
        </div>
      ) : null}

      {homeIn > 0.01 ? (
        <div style={{position: 'absolute', inset: 0, opacity: homeIn}}>
          <div style={{
            position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', fontFamily, fontSize: 96, fontWeight: 600,
            letterSpacing: '-.035em', lineHeight: 1.08, color: C.text,
          }}>Watch a brain watch<br />your video.</div>
        </div>
      ) : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: fadeBlack, pointerEvents: 'none'}} />
    </Stage>
  );
};
