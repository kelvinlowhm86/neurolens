import React from 'react';
import {useCurrentFrame} from 'remotion';
import {C, fontFamily} from '../../edit/ui';
import {Signpost, Stage, easeInOut, easeOut, lerp} from '../common';
import {DURATION, REPORT, SHOT, URL_TEXT} from './timeline';

const fmt = (s: number) => `0:${String(s).padStart(2, '0')}`;
const range = `${fmt(REPORT.moment.start)}–${fmt(REPORT.moment.end)}`;

const CARD = {x: 160, y: 100, w: 1600, h: 740};

const Row: React.FC<{label: string; text: string; top: number; opacity: number; hot?: boolean; size?: number}> = ({
  label, text, top, opacity, hot, size = 40,
}) => (
  <div style={{
    position: 'absolute', left: 60, right: 60, top, display: 'flex', gap: 40, alignItems: 'flex-start', opacity,
    transform: `translateY(${(1 - opacity) * 14}px)`,
  }}>
    <div style={{width: 250, flexShrink: 0, fontFamily, fontSize: 44, fontWeight: 600, letterSpacing: '-.01em', color: hot ? C.hot : C.muted, paddingTop: 2}}>{label}</div>
    <div style={{flex: 1, fontFamily, fontSize: size, lineHeight: 1.3, fontWeight: 500, letterSpacing: '-.01em', color: hot ? C.text : '#CDD1D7'}}>{text}</div>
  </div>
);

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();

  // 9: the report card. Headline first, then the three rows of one moment, then the Do line alone.
  const cardIn = easeOut(frame, SHOT['9a'].start + 2, SHOT['9a'].start + 20);
  const headlineOut = easeInOut(frame, SHOT['9b'].start + 4, SHOT['9b'].start + 24);
  const rowsIn = (n: number) => easeOut(frame, SHOT['9b'].start + 18 + n * 40, SHOT['9b'].start + 38 + n * 40);
  const rowsQuiet = easeInOut(frame, SHOT['9d'].start + 4, SHOT['9d'].start + 22);
  const cardOut = 1 - easeOut(frame, SHOT['10a'].start - 6, SHOT['10a'].start + 6);
  const bedrock = easeOut(frame, SHOT['9c'].start + 6, SHOT['9c'].start + 20) * (1 - easeOut(frame, SHOT['9d'].start, SHOT['9d'].start + 12));
  const push = 1 + 0.04 * easeInOut(frame, SHOT['9d'].start + 10, SHOT['9d'].end);

  // 10: two sentences.
  const first = easeOut(frame, SHOT['10a'].start + 4, SHOT['10a'].start + 22);
  const second = easeOut(frame, SHOT['10b'].start + 4, SHOT['10b'].start + 24);
  const firstDim = lerp(1, 0.38, easeInOut(frame, SHOT['10b'].start, SHOT['10b'].start + 16));
  const closingOut = 1 - easeOut(frame, SHOT['10b'].end - 8, SHOT['10b'].end + 4);

  // 11: the home page, then the address.
  const homeIn = easeOut(frame, SHOT['11'].start + 2, SHOT['11'].start + 22);
  const urlIn = easeOut(frame, SHOT['11'].start + 30, SHOT['11'].start + 52);
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
            <div style={{
              position: 'absolute', left: 80, right: 80, top: 0, bottom: 0, display: 'flex', alignItems: 'center', opacity: 1 - headlineOut,
              fontFamily, fontSize: 64, lineHeight: 1.2, fontWeight: 600, letterSpacing: '-.025em', color: C.text,
            }}>{REPORT.headline}</div>

            <div style={{position: 'absolute', left: 60, top: 56, fontFamily, fontSize: 44, fontWeight: 600, color: C.muted, opacity: headlineOut * (1 - rowsQuiet), fontVariantNumeric: 'tabular-nums'}}>{range}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, opacity: 1}}>
              <Row label="On screen" text={REPORT.moment.what_happens} top={150} opacity={rowsIn(0) * (1 - rowsQuiet)} />
              <Row label="Brain" text={REPORT.moment.brain_response} top={310} opacity={rowsIn(1) * (1 - rowsQuiet)} />
              <Row label="Do" text={REPORT.moment.suggestion ?? ''} hot top={520} opacity={rowsIn(2) * (1 - rowsQuiet)} />
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
            position: 'absolute', left: 0, right: 0, top: 210, textAlign: 'center', fontFamily, fontSize: 96, fontWeight: 600,
            letterSpacing: '-.035em', lineHeight: 1.08, color: C.text, opacity: lerp(1, 0.78, urlIn),
          }}>Watch a brain watch<br />your video.</div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 500, display: 'flex', justifyContent: 'center', opacity: lerp(1, 0.78, urlIn)}}>
            <div style={{
              fontFamily, fontSize: 48, fontWeight: 600, letterSpacing: '-.01em', color: '#0B0C0F', background: '#ECEDEF',
              borderRadius: 18, padding: '26px 64px 28px',
            }}>Explore samples</div>
          </div>
          <div style={{
            position: 'absolute', left: 0, right: 0, top: 740, textAlign: 'center', fontFamily, fontSize: 84, fontWeight: 600,
            letterSpacing: '-.025em', color: C.hot, opacity: urlIn, transform: `translateY(${(1 - urlIn) * 14}px)`,
            textShadow: '0 0 60px rgba(255,138,42,.3)',
          }}>{URL_TEXT}</div>
        </div>
      ) : null}

      <div style={{position: 'absolute', inset: 0, background: '#000', opacity: fadeBlack, pointerEvents: 'none'}} />
    </Stage>
  );
};
