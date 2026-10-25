import React from 'react';
import {
  AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence,
  interpolate, spring, staticFile, useCurrentFrame,
} from 'remotion';
import {getInfo} from '@remotion/google-fonts/Geist';
import {loadFontFromInfo} from '@remotion/google-fonts/from-info';
import bunny from './bunny.json';
import sintel from './sintel.json';
import {beats, cuts} from './beats';

// Use Remotion's Google Fonts loader, with every URL replaced by a local npm-supplied file.
// No font request leaves localhost, including during stills and final rendering.
const info = getInfo();
const localInfo = {...info, fonts: {normal: Object.fromEntries(
  ['400', '500', '600'].map(w => [w, {latin: staticFile(`geist-${w}.woff2`)}]),
)}, subsets: ['latin'], variable: undefined};
const {fontFamily} = loadFontFromInfo(localInfo, 'normal', {weights: ['400', '500', '600'], subsets: ['latin']});

const C = {bg: '#0E0F11', surface: '#17181B', text: '#ECEDEF', muted: '#A0A6AF', rule: '#24262B', hot: '#FF8A2A', cool: '#6F7E99'};
const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const ease = (f: number, a: number, b: number) => interpolate(f, [a, b], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
const enter = (f: number, delay = 0, damping = 20) => spring({frame: f - delay, fps: 30, config: {damping, stiffness: 150, mass: .8}});
const at = (values: number[], time: number) => {
  const t = clamp(time, 0, values.length - 1), i = Math.floor(t), u = t - i;
  return values[i] + (values[Math.min(i + 1, values.length - 1)] - values[i]) * u;
};
const signed = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(2)}`;
const clock = (n: number) => `00:${Math.floor(n).toString().padStart(2, '0')}`;
const mean = (arrays: number[][]) => arrays[0].map((_, i) => arrays.reduce((s, a) => s + a[i], 0) / arrays.length);
const groupData = {
  See: mean([bunny.signals.faces, bunny.signals.bodies, bunny.signals.scenes, bunny.signals.visual]),
  Hear: mean([bunny.signals.sound, bunny.signals.speech]),
  Understand: bunny.signals.social,
};

const pulse = (globalFrame: number) => {
  const last = [...beats].reverse().find(b => b.frame <= globalFrame);
  return last ? Math.exp(-(globalFrame - last.frame) / 5) * Math.min(last.strength, 1.1) : 0;
};

const Stage: React.FC<{children: React.ReactNode; glow?: number}> = ({children, glow = 1}) => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{background: C.bg, color: C.text, fontFamily, overflow: 'hidden', perspective: 1800}}>
    <AbsoluteFill style={{background: `radial-gradient(ellipse at ${53 + Math.sin(f / 130) * 5}% 56%, rgba(255,138,42,${.035 * glow}), transparent 56%)`}} />
    <div style={{position: 'absolute', width: 1200, height: 500, bottom: -400, left: 360, background: '#07080a', filter: 'blur(80px)', borderRadius: '50%'}} />
    {children}
  </AbsoluteFill>;
};

const Brand: React.FC<{opacity?: number}> = ({opacity = 1}) => <div style={{position: 'absolute', left: 96, top: 63, opacity, display: 'flex', alignItems: 'center', gap: 13, fontSize: 25, fontWeight: 500, letterSpacing: -.7}}>
  NeuroLens
</div>;

const Word: React.FC<{text: string; frame: number; delay: number; size?: number; color?: string}> = ({text, frame, delay, size, color}) => {
  const p = enter(frame, delay);
  return <span style={{display: 'inline-block', opacity: clamp((frame - delay) / 4), transform: `translateY(${(1 - p) * 55}px) scale(${.94 + p * .06})`, filter: `blur(${(1 - clamp(p)) * 10}px)`, fontSize: size, color, whiteSpace: 'pre'}}>{text}</span>;
};

const Headline: React.FC<{children: React.ReactNode; top?: number; left?: number; size?: number; center?: boolean}> = ({children, top = 132, left = 150, size = 92, center = false}) =>
  <div style={{position: 'absolute', top, left: center ? 0 : left, width: center ? '100%' : undefined, textAlign: center ? 'center' : 'left', fontWeight: 600, fontSize: size, lineHeight: 1.08, letterSpacing: '-.055em'}}>{children}</div>;

const Legend: React.FC<{label: string; color?: string; dashed?: boolean}> = ({label, color = C.hot, dashed = false}) => <div style={{display: 'flex', alignItems: 'center', gap: 12, fontSize: 22, color: C.muted}}>
  <div style={{width: 31, borderTop: `3px ${dashed ? 'dashed' : 'solid'} ${color}`}} />{label}
</div>;

const VideoCard: React.FC<{clip?: 'bunny' | 'sintel'; start?: number; width?: number; caption?: boolean; style?: React.CSSProperties; time?: number}> = ({clip = 'bunny', start = 12, width = 850, caption = true, style, time}) => {
  const f = useCurrentFrame();
  return <div style={{width, borderRadius: 26, padding: 7, background: 'linear-gradient(140deg, #46484d, #24262b 23%, #17181b 65%, #35373b)', boxShadow: '0 45px 100px #0009, 0 2px 2px #0008', overflow: 'hidden', ...style}}>
    <div style={{position: 'relative', width: '100%', height: width * 9 / 16, borderRadius: 20, overflow: 'hidden', background: '#0b0c0d'}}>
      <OffthreadVideo src={staticFile(clip === 'bunny' ? 'bbb_720p_55s.mp4' : 'sintel_trailer_720p.mp4')} startFrom={Math.round(start * 30)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      {caption && <><div style={{position: 'absolute', inset: 0, background: 'linear-gradient(transparent 65%, #000b)'}} />
        <div style={{position: 'absolute', bottom: 22, left: 26, right: 26, display: 'flex', justifyContent: 'space-between', fontSize: 20, fontWeight: 500, textShadow: '0 2px 6px #000'}}>
          <span>{clip === 'bunny' ? 'Big Buck Bunny' : 'Sintel'}</span><span style={{fontVariantNumeric: 'tabular-nums'}}>{clock(time ?? start + f / 30)}</span>
        </div></>}
    </div>
  </div>;
};

// Straight segments join actual one-second samples. The sub-second playhead uses linear interpolation.
const Plot: React.FC<{
  values: number[]; second?: number; width?: number; height?: number; range?: [number, number];
  color?: string; comparison?: number[]; comparisonColor?: string; comparisonOpacity?: number;
  comparisonDashed?: boolean; window?: number; axes?: boolean; progress?: number; label?: string;
}> = ({values, second, width = 1510, height = 235, range = [-.4, .55], color = C.hot, comparison, comparisonColor = C.cool, comparisonOpacity = 1, comparisonDashed = false, window = 30, axes = true, progress, label}) => {
  const pad = axes ? 53 : 9, top = axes ? 15 : 9, bottom = axes ? 39 : 9;
  const innerW = width - pad - 20, innerH = height - top - bottom;
  const x = (t: number) => pad + t / window * innerW;
  const y = (v: number) => top + (range[1] - v) / (range[1] - range[0]) * innerH;
  const end = progress === undefined ? (second ?? window) : window * clamp(progress);
  const path = (data: number[]) => {
    const points = Array.from({length: Math.floor(Math.min(end, window, data.length - 1)) + 1}, (_, i) => [x(i), y(data[i])]);
    if (end % 1 && end < data.length - 1) points.push([x(end), y(at(data, end))]);
    return points.map(([xx, yy], i) => `${i ? 'L' : 'M'}${xx.toFixed(2)},${yy.toFixed(2)}`).join(' ');
  };
  const p = path(values), cp = comparison ? path(comparison) : '';
  const zero = y(0), px = x(clamp(second ?? end, 0, window));
  return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{overflow: 'visible'}}>
    <defs><linearGradient id={`fill-${label || 'main'}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} stopOpacity=".16" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
    {axes && [-.3,0,.3].map(v => <g key={v}><line x1={pad} x2={width - 20} y1={y(v)} y2={y(v)} stroke={v === 0 ? '#4A4D54' : C.rule} strokeDasharray={v === 0 ? '3 7' : undefined} /><text x={pad - 18} y={y(v) + 6} fill={C.muted} fontSize="17" textAnchor="end">{v > 0 ? `+${v.toFixed(1)}` : v === 0 ? '0' : '−0.3'}</text></g>)}
    {!axes && <line x1={pad} x2={width - 20} y1={zero} y2={zero} stroke={C.rule} />}
    {axes && [0,10,20,30,40,50].filter(t => t <= window).map(t => <text key={t} x={x(t)} y={height - 7} fill={C.muted} fontSize="17" textAnchor="middle">{t}s</text>)}
    {p && <><path d={`${p} L${x(Math.min(end, window))},${zero} L${pad},${zero} Z`} fill={`url(#fill-${label || 'main'})`} />
      <path d={p} fill="none" stroke={color} strokeWidth="10" strokeOpacity=".08" strokeLinejoin="round" strokeLinecap="round" />
      <path d={p} fill="none" stroke={color} strokeWidth={axes ? 3.7 : 2.6} strokeLinejoin="round" strokeLinecap="round" /></>}
    {comparison && <path d={cp} fill="none" stroke={comparisonColor} opacity={comparisonOpacity} strokeWidth="3.5" strokeDasharray={comparisonDashed ? '10 9' : undefined} strokeLinejoin="round" strokeLinecap="round" />}
    {second !== undefined && <g>
      <line x1={px} x2={px} y1={top - 4} y2={height - bottom + 2} stroke={C.text} strokeOpacity=".27" />
      <circle cx={px} cy={y(at(values, second))} r="12" fill={color} opacity=".13" /><circle cx={px} cy={y(at(values, second))} r="5" fill={color} stroke={C.bg} strokeWidth="2" />
      {comparison && <circle cx={px} cy={y(at(comparison, second))} r="5" opacity={comparisonOpacity} fill={comparisonColor} stroke={C.bg} strokeWidth="2" />}
    </g>}
  </svg>;
};

const ColdOpen: React.FC = () => {
  const f = useCurrentFrame();
  const second = f >= 75;
  const out = 1 - ease(f, 126, 135);
  return <Stage glow={0}>
    <AbsoluteFill style={{background: '#000', opacity: out}}>
      <div style={{position: 'absolute', left: 0, top: 332, width: '100%', textAlign: 'center', fontSize: 110, fontWeight: 600, lineHeight: 1.13, letterSpacing: '-.055em'}}>
        {!second ? <><Word text="Every second" frame={f} delay={15} /><br /><Word text="of a video" frame={f} delay={45} /></> : <><Word text="lands somewhere" frame={f} delay={75} /><br /><Word text="in the brain." frame={f} delay={90} /></>}
      </div>
      <div style={{position: 'absolute', left: 0, bottom: 166, width: '100%', display: 'flex', justifyContent: 'center', opacity: ease(f, 20, 40)}}><div style={{width: 42, height: 3, background: C.hot, borderRadius: 2, transform: `scaleX(${.8 + pulse(f) * .4})`}} /></div>
    </AbsoluteFill>
  </Stage>;
};

const BrainScene: React.FC = () => {
  const f = useCurrentFrame(), p = enter(f, 0, 24), global = f + cuts.brain;
  const drift = Math.sin(f / 140);
  return <Stage>
    <Brand opacity={ease(f, 10, 28)} />
    <Headline top={145} size={72}><Word text="Meet your video's response." frame={f} delay={8} /></Headline>
    <VideoCard width={880} start={12} style={{position: 'absolute', left: 133, top: 352, transform: `translateY(${(1-p)*100}px) perspective(1600px) rotateY(${5+drift*1.2}deg) rotateX(2deg) scale(${.96+p*.04})`, opacity: clamp(f / 8)}} />
    <div style={{position: 'absolute', width: 835, height: 710, left: 1018, top: 223, transform: `translateY(${(1-enter(f, 13))*55}px) scale(${.98+ease(f,0,180)*.05})`, opacity: ease(f, 10, 25)}}>
      <div style={{position: 'absolute', inset: '20% 10%', background: C.hot, opacity: .04+pulse(global)*.025, filter: 'blur(70px)', borderRadius: '50%'}} />
      <Img src={staticFile(`brain/${Math.min(f,179).toString().padStart(3,'0')}.png`)} style={{width: '100%', filter: 'drop-shadow(0 30px 35px #0008)'}} />
    </div>
    <div style={{position: 'absolute', left: 1220, top: 900, fontSize: 23, color: C.muted, opacity: ease(f, 25, 45)}}>Predicted brain response</div>
  </Stage>;
};

const OverallScene: React.FC = () => {
  const f = useCurrentFrame(), p = enter(f), time = 18+f/30;
  return <Stage>
    <Brand />
    <Headline top={132} size={89}><Word text="See it. " frame={f} delay={0} /><Word text="Second by second." frame={f} delay={15} /></Headline>
    <VideoCard width={760} start={18} style={{position:'absolute', left:168, top:292, opacity:clamp(f/6), transform:`perspective(1600px) rotateY(3deg) translateY(${(1-p)*60}px)`}} />
    <div style={{position:'absolute', left:1068, top:337, width:560, opacity:ease(f,10,28), transform:`translateY(${(1-enter(f,10))*35}px)`}}>
      <div style={{fontSize:27, color:C.muted}}>Overall response</div>
      <div style={{fontSize:108, lineHeight:1.24, fontWeight:500, color:C.hot, fontVariantNumeric:'tabular-nums', letterSpacing:'-.06em'}}>{signed(at(bunny.signals.overall,time))}</div>
      <div style={{width:470, height:1, background:C.rule, margin:'24px 0'}} />
      <div style={{fontSize:23, color:C.muted}}>0 = typical viewing</div>
    </div>
    <div style={{position:'absolute', left:160, top:775, opacity:ease(f,2,20), transform:`translateY(${(1-enter(f,4))*45}px)`}}>
      <Plot values={bunny.signals.overall} second={time} width={1580} height={230} />
    </div>
  </Stage>;
};

const names = ['Faces','Bodies','Scenes','Visual','Sound','Speech','Social'] as const;
const signalArrays = names.map(n => bunny.signals[n.toLowerCase() as keyof typeof bunny.signals]);
const chipInitial = [[145,334],[548,334],[951,334],[1354,334],[346,545],[749,545],[1152,545]];
const chipFinal = [[150,379],[150,483],[150,587],[150,691],[720,379],[720,483],[1290,379]];
const groups = [
  {name:'See',count:4,x:126,data:groupData.See},
  {name:'Hear',count:2,x:696,data:groupData.Hear},
  {name:'Understand',count:1,x:1266,data:groupData.Understand},
];
const SignalScene: React.FC = () => {
  const f=useCurrentFrame(), gather=ease(f,78,113), time=25+f/30;
  return <Stage>
    <Brand />
    <Headline top={139} center size={92}><Word text="Seven signals." frame={f} delay={0} /></Headline>
    <div style={{position:'absolute', top:265, left:0,width:'100%',textAlign:'center',fontSize:27,color:C.muted,opacity:1-gather}}>A closer look at every moment.</div>
    {groups.map((g,i)=><div key={g.name} style={{position:'absolute',left:g.x,top:292,width:528,height:660,border:`1px solid ${C.rule}`,borderRadius:32,background:'linear-gradient(150deg, #1b1c1f99, #17181b55)',boxShadow:'0 30px 70px #0003',opacity:gather,transform:`translateY(${(1-gather)*30}px)`}}>
      <div style={{padding:'28px 32px',fontSize:40,fontWeight:500,letterSpacing:'-.04em'}}>{g.name}</div>
      <div style={{position:'absolute',left:27,bottom:25}}><Plot values={g.data} window={54} progress={ease(f,108+i*8,165+i*8)} width={470} height={130} range={[-.35,.9]} axes={false} label={g.name} /></div>
    </div>)}
    {names.map((name,i)=>{
      const delay=[0,8,15,23,30,38,45][i], p=enter(f,delay,17);
      const x=interpolate(gather,[0,1],[chipInitial[i][0],chipFinal[i][0]]),y=interpolate(gather,[0,1],[chipInitial[i][1],chipFinal[i][1]]);
      const width=interpolate(gather,[0,1],[371,480]),height=interpolate(gather,[0,1],[172,84]);
      return <div key={name} style={{position:'absolute',left:x,top:y,width,height,borderRadius:20,padding:'23px 26px',boxSizing:'border-box',background:C.surface,border:'1px solid #303239',boxShadow:`0 ${16*(1-gather)}px ${40*(1-gather)}px #0005`,opacity:clamp((f-delay)/4),transform:`scale(${.72+p*.28}) translateY(${(1-p)*35}px)`}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline'}}><span style={{fontSize:27,fontWeight:500,letterSpacing:'-.025em'}}>{name}</span><span style={{fontSize:26,color:C.hot,fontVariantNumeric:'tabular-nums'}}>{signed(at(signalArrays[i],time))}</span></div>
        <div style={{position:'absolute',left:20,right:20,bottom:9,opacity:1-gather}}><Plot values={signalArrays[i]} window={54} progress={1} width={330} height={66} range={[-.35,.9]} axes={false} label={name} /></div>
      </div>;
    })}
  </Stage>;
};

const MutedScene: React.FC = () => {
  const f=useCurrentFrame(), time=25+f/30, split=ease(f,22,60);
  return <Stage glow={.7}>
    <Brand />
    <Headline top={140} size={98}><Word text="What if it's muted?" frame={f} delay={0} /></Headline>
    <VideoCard width={660} start={25} style={{position:'absolute',left:154,top:325,transform:`perspective(1600px) rotateY(${3-Math.sin(f/80)}deg)`}} />
    <div style={{position:'absolute',left:955,top:370,opacity:ease(f,12,30)}}>
      <div style={{display:'flex',gap:48,marginBottom:28}}><Legend label="With audio" /><Legend label="Muted" color={C.cool} dashed /></div>
      <div style={{display:'flex',gap:62,alignItems:'baseline'}}>
        <span style={{fontSize:86,fontWeight:500,color:C.hot,letterSpacing:'-.06em',fontVariantNumeric:'tabular-nums'}}>{signed(at(bunny.signals.overall,time))}</span>
        <span style={{fontSize:86,fontWeight:500,color:C.cool,letterSpacing:'-.06em',fontVariantNumeric:'tabular-nums',opacity:split}}>{signed(at(bunny.muted.overall,time))}</span>
      </div>
      <div style={{fontSize:23,color:C.muted,marginTop:18}}>Same video. Without the sound.</div>
    </div>
    <div style={{position:'absolute',left:160,top:773,opacity:ease(f,2,15)}}>
      <Plot values={bunny.signals.overall} comparison={bunny.muted.overall} comparisonDashed comparisonOpacity={split} second={time} width={1580} height={230} />
    </div>
  </Stage>;
};

const CompareScene: React.FC = () => {
  const f=useCurrentFrame(), time=26+f/30;
  return <Stage glow={.6}>
    <Brand />
    <Headline top={130} size={100}><Word text="Compare." frame={f} delay={0} /></Headline>
    <div style={{position:'absolute',left:1180,top:172,display:'flex',gap:46,opacity:ease(f,10,26)}}><Legend label="Big Buck Bunny" /><Legend label="Sintel" color={C.cool} /></div>
    <VideoCard width={690} clip="bunny" start={26} style={{position:'absolute',left:208,top:295,transform:`perspective(1600px) rotateY(${3+Math.sin(f/100)}deg) translateX(${(1-enter(f))*-90}px)`,opacity:clamp(f/7)}} />
    <VideoCard width={690} clip="sintel" start={26} style={{position:'absolute',left:1015,top:295,transform:`perspective(1600px) rotateY(${-3-Math.sin(f/100)}deg) translateX(${(1-enter(f,8))*90}px)`,opacity:clamp((f-8)/7)}} />
    <div style={{position:'absolute',left:160,top:750,opacity:ease(f,7,25)}}><Plot values={bunny.signals.overall} comparison={sintel.signals.overall} second={time} window={40} width={1580} height={258} /></div>
  </Stage>;
};

const HeroScene: React.FC = () => {
  const f=useCurrentFrame(), p=enter(f,0,24);
  const pp=pulse(f+cuts.hero);
  return <Stage glow={1.1}>
    <div style={{position:'absolute',left:0,right:0,top:200,height:640,background:`radial-gradient(ellipse at 50% 50%, rgba(255,138,42,${.055+pp*.014}), transparent 65%)`}} />
    <div style={{position:'absolute',top:365,left:0,width:'100%',textAlign:'center',opacity:clamp(f/8),transform:`translateY(${(1-p)*45}px) scale(${.95+p*.05})`}}>
      <div style={{fontSize:164,fontWeight:600,letterSpacing:'-.065em',lineHeight:1.3,marginTop:18}}>NeuroLens</div>
      <div style={{fontSize:39,fontWeight:400,color:C.muted,letterSpacing:'-.028em',marginTop:13,opacity:ease(f,18,38),transform:`translateY(${(1-enter(f,18))*20}px)`}}>See how your video lands.</div>
    </div>
  </Stage>;
};

const CreditsScene: React.FC = () => {
  const f=useCurrentFrame();
  return <Stage glow={0}>
    <div style={{position:'absolute',left:0,top:303,width:'100%',textAlign:'center',fontSize:59,fontWeight:500,letterSpacing:'-.045em',opacity:.8}}>NeuroLens</div>
    <div style={{position:'absolute',left:210,right:210,top:590,textAlign:'center',fontSize:25,lineHeight:1.8,color:C.muted,opacity:ease(f,0,8)}}>
      <div>Predicted brain response from TRIBE v2 (Meta, CC BY-NC).</div>
      <div>Big Buck Bunny and Sintel © Blender Foundation, CC BY 3.0.</div>
      <div>Music: AudioEra.</div>
    </div>
  </Stage>;
};

export const Trailer: React.FC = () => {
  const f=useCurrentFrame();
  return <AbsoluteFill style={{background:C.bg,fontFamily}}>
    <Audio src={staticFile('music-edit.wav')} volume={1} />
    <Sequence from={0} durationInFrames={cuts.brain}><ColdOpen /></Sequence>
    <Sequence from={cuts.brain} durationInFrames={cuts.overall-cuts.brain}><BrainScene /></Sequence>
    <Sequence from={cuts.overall} durationInFrames={cuts.signals-cuts.overall}><OverallScene /></Sequence>
    <Sequence from={cuts.signals} durationInFrames={cuts.muted-cuts.signals}><SignalScene /></Sequence>
    <Sequence from={cuts.muted} durationInFrames={cuts.compare-cuts.muted}><MutedScene /></Sequence>
    <Sequence from={cuts.compare} durationInFrames={cuts.hero-cuts.compare}><CompareScene /></Sequence>
    <Sequence from={cuts.hero} durationInFrames={cuts.credits-cuts.hero}><HeroScene /></Sequence>
    <Sequence from={cuts.credits} durationInFrames={cuts.end-cuts.credits}><CreditsScene /></Sequence>
    <AbsoluteFill style={{background:'#000',opacity:ease(f,1273,1289),pointerEvents:'none'}} />
  </AbsoluteFill>;
};
