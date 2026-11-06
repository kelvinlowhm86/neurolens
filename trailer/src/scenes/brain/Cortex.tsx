import React, {useEffect, useLayoutEffect, useMemo} from 'react';
import {useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {BrainData, paintBrain} from './data';
const vertexShader = `
attribute vec3 vcol;
attribute float activity;
attribute float base;
varying float vBase;
varying vec3 vColor;
varying vec3 vNormal;
varying vec3 vView;
varying float vActivity;
void main() {
  vColor = vcol; vActivity = activity; vBase = base;
  vec4 mv = modelViewMatrix * vec4(position, 1.);
  vView = normalize(-mv.xyz); vNormal = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * mv;
}`;
const fragmentShader = `
varying float vBase;
varying vec3 vColor;
varying vec3 vNormal;
varying vec3 vView;
varying float vActivity;
void main() {
  vec3 n = normalize(vNormal), v = normalize(vView);
  vec3 key = normalize(vec3(.35, .45, .9));
  float d = max(0., dot(n, key));
  float rim = pow(1. - max(0., dot(n, v)), 2.6);
  // Trailer look: dark graphite cortex (sulcal depth kept as shading), cool rim light,
  // and the real response tail (vActivity, from the app's fixed colour range) as emitted light.
  vec3 colour = vec3(.17, .185, .21) * (.45 + 1.6 * (vBase - .6)) * (.45 + .85 * d);
  float sheen = pow(max(0., dot(reflect(-key, n), v)), 28.);
  colour += vec3(.75, .82, .95) * sheen * .18;
  colour += vec3(.40, .50, .66) * rim * .55;
  float glow = smoothstep(0., .10, vActivity) * (.16 + .62 * vActivity) * (.45 + .55 * d) * (.7 + .6 * (vBase - .6) / .37);
  colour += mix(vec3(1., .36, .04), vec3(1., .62, .18), clamp(vActivity, 0., 1.)) * glow;
  gl_FragColor = vec4(colour, 1.);
}`;
const glowVertex = `
attribute float activity;
varying float vActivity;
varying vec3 vNormal;
varying vec3 vView;
void main() {
 vActivity = activity;
 vec4 mv = modelViewMatrix * vec4(position + normal * 2.2, 1.);
 vNormal = normalize(normalMatrix * normal); vView = normalize(-mv.xyz);
 gl_Position = projectionMatrix * mv;
}`;
const glowFragment = `
varying float vActivity;
varying vec3 vNormal;
varying vec3 vView;
void main() {
 float fresnel = pow(1. - abs(dot(normalize(vNormal), normalize(vView))), 2.);
 gl_FragColor = vec4(1., .40, .07, min(1., vActivity * 1.4) * (.15 + fresnel) * .3);
}`;
export const Cortex: React.FC<{data: BrainData; second: number; progress: number}> = ({data, second, progress}) => {
  const {camera} = useThree();
  const resources = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(data.faces, 1)); geometry.computeVertexNormals();
    const colours = new Float32Array(data.base.length * 3), activity = new Float32Array(data.base.length);
    const colourAttribute = new THREE.BufferAttribute(colours, 3).setUsage(THREE.DynamicDrawUsage);
    const activityAttribute = new THREE.BufferAttribute(activity, 1).setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('vcol', colourAttribute); geometry.setAttribute('activity', activityAttribute);
    geometry.setAttribute('base', new THREE.BufferAttribute(data.base, 1));
    const material = new THREE.ShaderMaterial({vertexShader, fragmentShader, toneMapped: false});
    const glow = new THREE.ShaderMaterial({vertexShader: glowVertex, fragmentShader: glowFragment, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false});
    return {geometry, material, glow, colours, activity, colourAttribute, activityAttribute};
  }, [data]);
  paintBrain(data, second, resources.colours, resources.activity);
  resources.colourAttribute.needsUpdate = true; resources.activityAttribute.needsUpdate = true;
  useLayoutEffect(() => {
    // The anatomy uses superior = Z, matching the product's OrbitControls camera.
    const azimuth = .22 + progress * .45, distance = 470 - progress * 95;
    camera.up.set(0, 0, 1);
    camera.position.set(Math.cos(azimuth) * -distance, Math.sin(azimuth) * -distance, 165 - progress * 13);
    camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  }, [camera, progress]);
  useEffect(() => () => {resources.geometry.dispose(); resources.material.dispose(); resources.glow.dispose();}, [resources]);
  return <group><mesh geometry={resources.geometry} material={resources.material}/><mesh geometry={resources.geometry} material={resources.glow} renderOrder={1}/></group>;
};
