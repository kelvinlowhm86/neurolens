import React, {useEffect, useState} from 'react';
import {ThreeCanvas} from '@remotion/three';
import {AbsoluteFill, OffthreadVideo, cancelRender, continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Cortex} from './brain/Cortex';
import {BrainData, parseBrain} from './brain/data';
let dataPromise: Promise<BrainData> | undefined;
const loadData = () => {
  if (!dataPromise) dataPromise = Promise.all(['fsaverage6_pial.bin', 'bbb_720p_55s/brain'].map(async (file) => {
    const response = await fetch(staticFile(file));
    if (!response.ok) throw new Error(`BrainHero asset ${file}: ${response.status}`);
    return response.arrayBuffer();
  })).then(([mesh, brain]) => parseBrain(mesh, brain));
  return dataPromise;
};
/** A live anatomical response map; source t seconds is used by both video and cortex. */
export const BrainHero: React.FC<{start?: number; layout?: 'card' | 'solo'}> = ({start = 12, layout = 'card'}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames, width, height} = useVideoConfig();
  const [hold] = useState(() => delayRender('Loading real cortical mesh and predictions'));
  const [data, setData] = useState<BrainData | null>(null);
  useEffect(() => {
    let mounted = true;
    loadData().then((loaded) => {if (mounted) {setData(loaded); continueRender(hold);}}).catch(cancelRender);
    return () => {mounted = false; continueRender(hold);};
  }, [hold]);
  const progress = Math.max(0, Math.min(1, frame / Math.max(1, durationInFrames - 1)));
  const sourceStart = Math.max(0, Math.round(start * fps)) / fps;
  const second = sourceStart + frame / fps;
  const drift = Math.sin(progress * Math.PI) * 10;
  const solo = layout === 'solo';
  return <AbsoluteFill style={{background: '#0E0F11', overflow: 'hidden'}}>
    <AbsoluteFill style={{background: solo
      ? 'radial-gradient(ellipse at 50% 47%, rgba(92,106,130,.30), transparent 52%), radial-gradient(ellipse at 50% 50%, rgba(255,138,42,.10), transparent 40%)'
      : 'radial-gradient(ellipse at 69% 48%, rgba(73,83,97,.13), transparent 54%)'}}/>
    {!solo && <div style={{position: 'absolute', left: 66 - progress * 16, top: 293 - drift, width: 825, height: 464, perspective: 1800, transform: `scale(${1 + progress * .035})`, transformOrigin: 'center'}}>
      <div style={{position: 'absolute', inset: 0, transformStyle: 'preserve-3d', transform: `rotateY(${-13 + progress * 3}deg) rotateX(3deg) rotateZ(-1deg)`}}>
        <div style={{position: 'absolute', inset: -2, borderRadius: 10, background: '#333a42', transform: 'translateZ(-8px)', boxShadow: '0 38px 85px rgba(0,0,0,.72)'}}/>
        <div style={{position: 'absolute', inset: 0, overflow: 'hidden', borderRadius: 8, background: '#111', boxShadow: '0 0 0 1px rgba(224,233,246,.19)'}}>
          <OffthreadVideo src={staticFile('bbb_720p_55s.mp4')} trimBefore={Math.round(sourceStart * fps)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}}/>
          <AbsoluteFill style={{background: 'linear-gradient(110deg, rgba(211,228,255,.065), transparent 45%, rgba(0,0,0,.16))'}}/>
        </div>
      </div>
    </div>}
    {!solo && <svg width={1920} height={1080} viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, width, height, opacity: .55}}>
      <defs><linearGradient id="brainHeroLink"><stop stopColor="#FF8A2A" stopOpacity="0"/><stop offset=".55" stopColor="#FF8A2A" stopOpacity=".35"/><stop offset="1" stopColor="#FF8A2A" stopOpacity="0"/></linearGradient></defs>
      <path d="M 870 530 C 947 530, 961 558, 1040 558" fill="none" stroke="url(#brainHeroLink)" strokeWidth="1"/>
    </svg>}
    {data && <ThreeCanvas width={solo ? 1500 : 1200} height={solo ? 1080 : 1040} style={{position: 'absolute', left: solo ? 210 : 770, top: solo ? 0 : 12, pointerEvents: 'none'}} camera={{fov: 28, near: .1, far: 3000, position: [-300, 300, 165], up: [0, 0, 1]}} gl={{alpha: true, antialias: true, preserveDrawingBuffer: true}} dpr={1}>
      <Cortex data={data} second={second} progress={progress}/>
    </ThreeCanvas>}
    <AbsoluteFill style={{pointerEvents: 'none', background: 'radial-gradient(ellipse at 51% 48%, transparent 38%, rgba(0,0,0,.30) 100%)'}}/>
  </AbsoluteFill>;
};
