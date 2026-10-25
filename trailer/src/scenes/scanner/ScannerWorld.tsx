import React, {useLayoutEffect, useMemo} from 'react';
import {useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {RectAreaLightUniformsLib} from 'three/addons/lights/RectAreaLightUniformsLib.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {mergeVertices} from 'three/addons/utils/BufferGeometryUtils.js';

const COLD = '#6F7E99';
const ICE = '#BCD0EB';
const smooth = (a: number, b: number, x: number) => {
  const v = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return v * v * (3 - 2 * v);
};

function Camera({progress, fly}: {progress: number; fly: number}) {
  const {camera} = useThree();
  useLayoutEffect(() => {
    // Slow start, then an ease-in acceleration through the open bore; fly stays 0 for the drained replay.
    const dolly = Math.pow(progress, 1.8);
    const base = new THREE.Vector3(6.4 - dolly * 5.2, 3.9 - dolly * 1.35, 9.6 - dolly * 5.6);
    const look = new THREE.Vector3(-0.1 + dolly * 0.1, 2.1 + dolly * 0.28, 0.9 - dolly * 2.1);
    if (fly > 0) {
      const s = Math.pow(Math.min(1, Math.max(0, fly)), 3);
      const keys = [
        {t: 0, p: [base.x, base.y, base.z], l: [look.x, look.y, look.z]},
        {t: .5, p: [0, 2.62, 1.35], l: [0, 2.56, -1.2]},
        {t: .8, p: [0, 2.62, -.5], l: [0, 2.58, -6]},
        {t: 1, p: [0, 2.62, -3.4], l: [0, 2.62, -9]},
      ] as const;
      let i = 0;
      while (i < keys.length - 2 && s > keys[i + 1].t) i++;
      const a = keys[i], b = keys[i + 1];
      const u = (s - a.t) / (b.t - a.t);
      camera.position.set(
        a.p[0] + (b.p[0] - a.p[0]) * u,
        a.p[1] + (b.p[1] - a.p[1]) * u,
        a.p[2] + (b.p[2] - a.p[2]) * u,
      );
      camera.lookAt(
        a.l[0] + (b.l[0] - a.l[0]) * u,
        a.l[1] + (b.l[1] - a.l[1]) * u,
        a.l[2] + (b.l[2] - a.l[2]) * u,
      );
    } else {
      camera.position.copy(base);
      camera.lookAt(look);
    }
    camera.updateProjectionMatrix();
  }, [camera, progress, fly]);
  return null;
}

function StudioReflections() {
  const {gl, scene} = useThree();
  useLayoutEffect(() => {
    RectAreaLightUniformsLib.init();
    const generator = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const environment = generator.fromScene(room, 0.04);
    scene.environment = environment.texture;
    scene.environmentIntensity = 0.13;
    room.dispose();
    generator.dispose();
    return () => {scene.environment = null; environment.dispose();};
  }, [gl, scene]);
  return null;
}

function RoundedBox({position, scale, color = '#536075', metalness = 0.35, roughness = 0.4, radius = 0.12}: {
  position: [number, number, number]; scale: [number, number, number]; color?: string;
  metalness?: number; roughness?: number; radius?: number;
}) {
  const geometry = useMemo(() => {
    const [w, h, d] = scale;
    const r = Math.min(radius, w / 2 - 0.001, h / 2 - 0.001, d / 2 - 0.001);
    const shape = new THREE.Shape();
    shape.moveTo(-w / 2 + r, -h / 2);
    shape.lineTo(w / 2 - r, -h / 2);
    shape.quadraticCurveTo(w / 2, -h / 2, w / 2, -h / 2 + r);
    shape.lineTo(w / 2, h / 2 - r);
    shape.quadraticCurveTo(w / 2, h / 2, w / 2 - r, h / 2);
    shape.lineTo(-w / 2 + r, h / 2);
    shape.quadraticCurveTo(-w / 2, h / 2, -w / 2, h / 2 - r);
    shape.lineTo(-w / 2, -h / 2 + r);
    shape.quadraticCurveTo(-w / 2, -h / 2, -w / 2 + r, -h / 2);
    const g = new THREE.ExtrudeGeometry(shape, {depth: d - 2 * r, bevelEnabled: true,
      bevelSegments: 8, steps: 1, bevelSize: r, bevelThickness: r, curveSegments: 16});
    g.translate(0, 0, -d / 2 + r);
    // ExtrudeGeometry splits faces at the bevel; weld them for continuous highlights.
    g.deleteAttribute('normal');
    g.deleteAttribute('uv');
    const welded = mergeVertices(g);
    welded.computeVertexNormals();
    g.dispose();
    return welded;
  }, [scale[0], scale[1], scale[2], radius]);
  return <mesh position={position} geometry={geometry} castShadow receiveShadow>
    <meshStandardMaterial color={color} metalness={metalness} roughness={roughness}/>
  </mesh>;
}

function Ellipsoid({position, scale, color = '#8993A2'}: {
  position: [number, number, number]; scale: [number, number, number]; color?: string;
}) {
  return <mesh position={position} scale={scale} castShadow receiveShadow>
    <sphereGeometry args={[1, 40, 28]}/>
    <meshStandardMaterial color={color} roughness={0.62} metalness={0.08}/>
  </mesh>;
}

function Limb({from, to, r1, r2}: {from: [number, number, number]; to: [number, number, number]; r1: number; r2: number}) {
  const {position, rotation, length} = useMemo(() => {
    const a = new THREE.Vector3(...from);
    const b = new THREE.Vector3(...to);
    const delta = b.clone().sub(a);
    return {position: a.add(b).multiplyScalar(0.5), length: delta.length(),
      rotation: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize())};
  }, [from.join(), to.join()]);
  return <mesh position={position} quaternion={rotation} castShadow receiveShadow>
    <cylinderGeometry args={[r2, r1, length, 24]}/>
    <meshStandardMaterial color="#8993A2" roughness={0.62} metalness={0.08}/>
  </mesh>;
}

function Patient() {
  const torso = useMemo(() => {
    const profile = [[0, 1.98], [0.2, 2.01], [0.3, 2.09], [0.34, 2.25],
      [0.32, 2.46], [0.27, 2.68], [0.26, 2.85], [0.28, 2.99], [0.23, 3.12], [0, 3.2]];
    const g = new THREE.LatheGeometry(profile.map(([r, z]) => new THREE.Vector2(r, z)), 64);
    g.rotateX(Math.PI / 2);
    g.scale(1, 0.66, 1);
    return g;
  }, []);
  const leg = useMemo(() => {
    const profile = [[0, 3.02], [0.1, 3.04], [0.14, 3.17], [0.145, 3.35],
      [0.12, 3.55], [0.1, 3.69], [0.098, 3.77], [0.106, 3.94], [0.086, 4.15],
      [0.06, 4.3], [0, 4.36]];
    const curve = new THREE.SplineCurve(profile.map(([r, z]) => new THREE.Vector2(r, z)));
    const g = new THREE.LatheGeometry(curve.getPoints(70), 40);
    g.rotateX(Math.PI / 2);
    return g;
  }, []);
  // Supine mannequin: head points into the bore (-Z), feet toward camera (+Z).
  return <group position={[0, 1.77, 0]}>
    <Ellipsoid position={[0, 0.12, 1.62]} scale={[0.235, 0.255, 0.29]}/>
    <Ellipsoid position={[0, 0.04, 1.96]} scale={[0.115, 0.13, 0.16]}/>
    <mesh geometry={torso} position={[0, 0.03, 0]} castShadow receiveShadow>
      <meshStandardMaterial color="#8993A2" roughness={0.62} metalness={0.08}/>
    </mesh>
    {[-1, 1].map((s) => <group key={s}>
      <Ellipsoid position={[s * 0.33, 0.03, 2.12]} scale={[0.14, 0.15, 0.17]}/>
      <Limb from={[s * 0.36, 0, 2.19]} to={[s * 0.43, -0.04, 2.65]} r1={0.105} r2={0.085}/>
      <Ellipsoid position={[s * 0.43, -0.04, 2.65]} scale={[0.085, 0.087, 0.09]}/>
      <Limb from={[s * 0.43, -0.04, 2.65]} to={[s * 0.42, -0.07, 3.12]} r1={0.085} r2={0.06}/>
      <Ellipsoid position={[s * 0.42, -0.07, 3.19]} scale={[0.075, 0.063, 0.14]}/>
      <mesh geometry={leg} position={[s * 0.16, -0.01, 0]} castShadow receiveShadow>
        <meshStandardMaterial color="#8993A2" roughness={0.62} metalness={0.08}/>
      </mesh>
      <Ellipsoid position={[s * 0.16, 0.045, 4.4]} scale={[0.105, 0.2, 0.17]}/>
    </group>)}
  </group>;
}

function ScannerHousing({progress}: {progress: number}) {
  const face = useMemo(() => {
    // Radius/axial-depth profile: a rounded annulus with a real, open bore.
    const profile = [[1.01, 0.18], [1.01, 0.6], [1.025, 0.75], [1.08, 0.86],
      [1.19, 0.94], [1.42, 0.97], [1.67, 0.93], [1.87, 0.82], [2.03, 0.65],
      [2.12, 0.4], [2.15, 0.16], [2.15, 0.08], [2.1, 0.02], [1.06, 0.02], [1.01, 0.18]];
    const curve = new THREE.SplineCurve(profile.map(([r, z]) => new THREE.Vector2(r, z)));
    return new THREE.LatheGeometry(curve.getPoints(120), 128);
  }, []);
  const warm = smooth(0.92, 1, progress);
  const ringColor = new THREE.Color(ICE).lerp(new THREE.Color('#FF8A2A'), warm);
  const scan = smooth(0.58, 0.88, progress);
  return <group position={[0, 2.38, 0]}>
    <mesh geometry={face} rotation={[Math.PI / 2, 0, 0]} castShadow receiveShadow>
      <meshStandardMaterial color="#ACB2BC" metalness={0.3} roughness={0.32}/>
    </mesh>
    <mesh position={[0, 0, -1.095]} rotation={[Math.PI / 2, 0, 0]} castShadow receiveShadow>
      <cylinderGeometry args={[2.145, 2.145, 2.35, 128, 1, true]}/>
      <meshStandardMaterial color="#515B68" metalness={0.45} roughness={0.38} side={THREE.DoubleSide}/>
    </mesh>
    <mesh position={[0, 0, -0.59]} rotation={[Math.PI / 2, 0, 0]} receiveShadow>
      <cylinderGeometry args={[1.008, 1.008, 2.6, 96, 1, true]}/>
      <meshStandardMaterial color="#253347" metalness={0.55} roughness={0.4} side={THREE.DoubleSide}/>
    </mesh>
    <mesh position={[0, 0, -1.93]}>
      <ringGeometry args={[1.01, 2.14, 128]}/>
      <meshStandardMaterial color="#3D495C" metalness={0.4} roughness={0.5} side={THREE.DoubleSide}/>
    </mesh>
    {/* Machined shadow gap makes the front read as a manufactured enclosure. */}
    <mesh position={[0, 0, 0.08]}>
      <torusGeometry args={[2.142, 0.012, 8, 128]}/>
      <meshStandardMaterial color="#111720" roughness={0.5}/>
    </mesh>
    <mesh position={[0, 0, 0.79]}>
      <torusGeometry args={[1.03, 0.013, 10, 128]}/>
      <meshBasicMaterial color={ringColor} toneMapped={false}/>
    </mesh>
    <mesh position={[0, 0, 0.79]}>
      <torusGeometry args={[1.03, 0.032, 10, 128]}/>
      <meshBasicMaterial color={ringColor} transparent opacity={0.14} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false}/>
    </mesh>
    {[0.74, 0.2, -0.45, -1.14, -1.75].map((z, i) => <group key={z}>
      <mesh position={[0, 0, z]}>
        <torusGeometry args={[0.997, i === 0 ? 0.012 : 0.008, 8, 96]}/>
        <meshBasicMaterial color={i === 0 ? ringColor : '#93AECF'} transparent opacity={i === 0 ? 0.74 : 0.28}/>
      </mesh>
    </group>)}
    {[-1, 1].map((s) => <group key={s}>
      <mesh position={[s * 0.7, 0.68, -0.4]}>
        <boxGeometry args={[0.035, 0.025, 2.5]}/>
        <meshBasicMaterial color="#B5C9E4" toneMapped={false}/>
      </mesh>
      <RoundedBox position={[s * 1.59, -0.4, 0.9]} scale={[0.14, 0.39, 0.025]} color="#293546" metalness={0.5} radius={0.018}/>
      <mesh position={[s * 1.59, -0.42, 0.928]}>
        <boxGeometry args={[0.026, 0.12, 0.006]}/>
        <meshBasicMaterial color="#91A9CB"/>
      </mesh>
    </group>)}
    <pointLight position={[0, 0.1, 0.0]} color={ringColor} intensity={7 + warm * 3} distance={4.5} decay={2}/>
    <pointLight position={[0, 0.1, 1.2]} color={ringColor} intensity={1.1 + warm * 0.7} distance={2.5} decay={2}/>
    {/* A quiet scanning band crosses the patient once, late in the shot. */}
    {scan > 0 && <mesh position={[0, -0.45, 1.3 - scan * 2.8]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[0.9, 0.045]}/>
      <meshBasicMaterial color="#CCDDF2" transparent opacity={Math.sin(scan * Math.PI) * 0.28} depthWrite={false} blending={THREE.AdditiveBlending}/>
    </mesh>}
  </group>;
}

function FloorGlow({position, scale, color, opacity}: {position: [number, number, number]; scale: [number, number, number]; color: string; opacity: number}) {
  const uniforms = useMemo(() => ({color: {value: new THREE.Color(color)}, strength: {value: opacity}}), [color, opacity]);
  return <mesh position={position} rotation={[-Math.PI / 2, 0, 0]} scale={scale}>
    <planeGeometry args={[1, 1]}/>
    <shaderMaterial transparent depthWrite={false} blending={THREE.AdditiveBlending} uniforms={uniforms}
      vertexShader={'varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }'}
      fragmentShader={'varying vec2 vUv; uniform vec3 color; uniform float strength; void main(){float r=length((vUv-.5)*2.); float a=exp(-r*r*5.)*(1.-smoothstep(.55,1.,r));gl_FragColor=vec4(color,a*strength);}'}/>
  </mesh>;
}

function Dust({progress}: {progress: number}) {
  const points = useMemo(() => {
    const a = new Float32Array(95 * 3);
    let seed = 4169;
    const rand = () => {seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296;};
    for (let i = 0; i < a.length; i += 3) {a[i] = (rand() - 0.5) * 12; a[i + 1] = 0.8 + rand() * 5.3; a[i + 2] = -3 + rand() * 10;}
    return a;
  }, []);
  return <points position={[-progress * 0.12, progress * 0.09, 0]}>
    <bufferGeometry><bufferAttribute attach="attributes-position" args={[points, 3]}/></bufferGeometry>
    <pointsMaterial color="#7D8CA6" size={0.012} transparent opacity={0.27} sizeAttenuation depthWrite={false}/>
  </points>;
}

function LightShaft() {
  return <mesh position={[-2.3, 3.55, -0.2]} rotation={[0, 0, -0.31]}>
    <planeGeometry args={[4.5, 9]}/>
    <shaderMaterial transparent depthWrite={false} side={THREE.DoubleSide} blending={THREE.AdditiveBlending}
      vertexShader={'varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }'}
      fragmentShader={'varying vec2 vUv; void main(){float beam=exp(-pow((vUv.x-.5)*7.,2.)); float ends=sin(vUv.y*3.14159);gl_FragColor=vec4(.36,.45,.61,beam*ends*ends*.035);}'}/>
  </mesh>;
}

export function ScannerWorld({progress, fly = 0}: {progress: number; fly?: number}) {
  const slide = smooth(0.08, 0.86, progress) * 2.25;
  return <>
    <color attach="background" args={['#0E0F11']}/>
    <fog attach="fog" args={['#0E0F11', 12, 32]}/>
    <Camera progress={progress} fly={fly}/>
    <StudioReflections/>
    <ambientLight intensity={0.29} color={COLD}/>
    <hemisphereLight args={['#8093AF', '#101319', 0.65]}/>
    <spotLight position={[-4.5, 8.5, 6]} color="#E3E8F0" intensity={165} angle={0.52} penumbra={0.8} distance={24} decay={2} castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0002}/>
    <spotLight position={[4, 6.5, -3]} color="#8795AE" intensity={150} angle={0.7} penumbra={0.8} distance={20} decay={2}/>
    <rectAreaLight position={[-3, 4.5, 4]} rotation={[-0.25, -0.4, 0]} color="#9BACC6" intensity={4.5} width={4} height={5}/>
    <pointLight position={[4, 3, 6]} color="#7888A0" intensity={15} distance={10} decay={2}/>
    <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[80, 80]}/>
      <meshStandardMaterial color="#101318" metalness={0.28} roughness={0.44}/>
    </mesh>
    <FloorGlow position={[0, 0.007, 0.2]} scale={[11, 9, 1]} color="#657C9C" opacity={0.105}/>
    <FloorGlow position={[0, 0.012, 2]} scale={[4.2, 9, 1]} color="#809ABB" opacity={0.035}/>
    <RoundedBox position={[0, 0.28, -0.55]} scale={[3.7, 0.53, 3.3]} color="#26303C" radius={0.2}/>
    <RoundedBox position={[0, 0.64, -0.55]} scale={[2.45, 0.9, 2.8]} color="#445164" radius={0.23}/>
    <ScannerHousing progress={progress}/>
    {/* Fixed table pedestal and precision slide rails. */}
    <RoundedBox position={[0, 0.17, 3.3]} scale={[1.18, 0.29, 2.9]} color="#202832" radius={0.08}/>
    <RoundedBox position={[0, 0.68, 3.1]} scale={[0.6, 1, 1.3]} color="#455368" radius={0.12}/>
    <RoundedBox position={[0, 1.22, 2.75]} scale={[0.84, 0.16, 5.1]} color="#2E3A4B" radius={0.05}/>
    {[-0.31, 0.31].map((x) => <RoundedBox key={x} position={[x, 1.34, 2.8]} scale={[0.055, 0.065, 5.1]} color="#77869C" metalness={0.8} roughness={0.22} radius={0.015}/>)}
    <group position={[0, 0, -slide]}>
      <RoundedBox position={[0, 1.47, 3.05]} scale={[1.05, 0.16, 4.75]} color="#66748A" metalness={0.55} radius={0.065}/>
      <RoundedBox position={[0, 1.59, 3.03]} scale={[0.94, 0.12, 4.65]} color="#3B485B" metalness={0.03} roughness={0.85} radius={0.055}/>
      <RoundedBox position={[0, 1.69, 1.61]} scale={[0.63, 0.11, 0.65]} color="#56657B" metalness={0.04} roughness={0.8} radius={0.04}/>
      <Patient/>
    </group>
    {/* Soft distant clinical architecture; no screen, lettering or readable UI. */}
    <mesh position={[-7, 4.4, -5.5]}>
      <boxGeometry args={[0.045, 5.8, 0.05]}/>
      <meshBasicMaterial color="#61738F" transparent opacity={0.1}/>
    </mesh>
    <mesh position={[1, 7, -6.5]} rotation={[0, 0, Math.PI / 2]}>
      <boxGeometry args={[0.025, 9, 0.05]}/>
      <meshBasicMaterial color="#6C7D98" transparent opacity={0.07}/>
    </mesh>
    <LightShaft/>
    <Dust progress={progress}/>
  </>;
}
