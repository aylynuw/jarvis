// JARVIS core, in 3D: a holographic HUD built from code (no outside models or images).
// A wire globe with a white-hot centre, flat rings of segmented arcs that turn at their own
// speeds, a tick dial, a voice ring of radial bars, tilted orbit rings and a shell of light.
// Shared by the dashboard (core3d.js) and the notch overlay (island.js).
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

// Resolve a CSS colour (oklch token or literal) to sRGB by painting one pixel.
export function cssColor(value) {
  const c = document.createElement("canvas");
  c.width = c.height = 1;
  const ctx = c.getContext("2d");
  ctx.fillStyle = value;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace);
}
const token = (name) => cssColor(getComputedStyle(document.documentElement).getPropertyValue(name).trim());

const PROFILES = {
  booting:   { spin: 0.3, glow: 0.4,  pulse: 0.0,  swirl: 0.2, bloom: 0.5,  beat: 0,   voice: 0 },
  idle:      { spin: 1.0, glow: 0.75, pulse: 0.0,  swirl: 0.4, bloom: 0.75, beat: 0.5, voice: 0.08 },
  listening: { spin: 1.5, glow: 1.3,  pulse: 0.35, swirl: 0.8, bloom: 1.05, beat: 2.6, voice: 0.55 },
  thinking:  { spin: 4.0, glow: 1.0,  pulse: 0.08, swirl: 3.0, bloom: 0.9,  beat: 1.4, voice: 0.15 },
  speaking:  { spin: 1.7, glow: 1.25, pulse: 0.0,  swirl: 1.0, bloom: 1.0,  beat: 0,   voice: 1 },
};

/**
 * mountCore(canvas, { getState, background, parallax, onStart })
 *   getState   () => "idle" | "listening" | "thinking" | "speaking" | "booting"
 *   background CSS colour behind the core (the bloom needs an opaque backdrop)
 *   parallax   follow the pointer a little
 *   tilt       how far the flat rings lean back; distance  camera distance; glow  bloom multiplier
 * Returns { redraw } or null when WebGL is unavailable.
 */
export function mountCore(canvas, { getState, background = "--color-paper", parallax = true, tilt = 0.32, distance = 11.1, glow = 1 } = {}) {
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  } catch {
    return null;
  }
  const BG = background.startsWith("--") ? token(background) : cssColor(background);
  const CYAN = token("--color-accent");
  const DEEP = token("--color-accent-dim");
  const STEEL = token("--color-ink-2");
  const WHITE = token("--color-ink");
  const WARM = token("--color-warm");

  renderer.setClearColor(BG, 1);
  renderer.toneMapping = THREE.NoToneMapping;   // tone mapping lifts the backdrop into a visible disc

  const scene = new THREE.Scene();
  scene.background = BG;
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  camera.position.set(0, 0, distance);

  const rig = new THREE.Group();     // pointer parallax
  const body = new THREE.Group();    // the hologram, tipped back so its flat rings read in depth
  body.rotation.x = -tilt;
  rig.add(body);
  scene.add(rig);

  const basic = (color, opacity, extra = {}) => new THREE.MeshBasicMaterial({
    color, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, ...extra,
  });
  const lineMat = (color, opacity) => new THREE.LineBasicMaterial({
    color, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending,
  });

  // ---- the centre: a white-hot point inside a cyan glow and a wire globe
  const heart = new THREE.Mesh(new THREE.SphereGeometry(0.2, 32, 24), new THREE.MeshBasicMaterial({ color: WHITE }));
  const halo = new THREE.Mesh(new THREE.SphereGeometry(0.42, 32, 24), basic(CYAN, 0.35));
  body.add(heart, halo);

  const globe = new THREE.Group();
  const globeMat = lineMat(CYAN, 0.42);
  const R = 0.78;
  for (let i = 1; i < 6; i++) {                       // latitudes
    const lat = -Math.PI / 2 + (i / 6) * Math.PI;
    const pts = [];
    for (let j = 0; j <= 64; j++) {
      const a = (j / 64) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * Math.cos(lat) * R, Math.sin(lat) * R, Math.sin(a) * Math.cos(lat) * R));
    }
    globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), globeMat));
  }
  for (let i = 0; i < 8; i++) {                       // longitudes
    const lon = (i / 8) * Math.PI;
    const pts = [];
    for (let j = 0; j <= 64; j++) {
      const a = (j / 64) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * Math.cos(lon) * R, Math.sin(a) * R, Math.cos(a) * Math.sin(lon) * R));
    }
    globe.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), globeMat));
  }
  body.add(globe);

  // ---- flat HUD rings: segmented arcs at several radii, each ring turning its own way
  const arcRings = [];
  const arcSpec = [
    { r: 1.02, w: 0.05, arcs: [[0, 0.9], [1.05, 0.5], [1.7, 1.1], [2.95, 0.3], [3.4, 1.4], [4.95, 1.0]], speed: 0.5, color: CYAN, o: 0.85 },
    { r: 1.2,  w: 0.012, arcs: [[0, Math.PI * 2]], speed: 0, color: STEEL, o: 0.35 },
    { r: 1.34, w: 0.11, arcs: [[0.2, 0.25], [0.6, 0.25], [1.0, 0.25], [2.4, 1.6], [4.6, 0.12], [4.8, 0.12], [5.0, 0.12]], speed: -0.32, color: CYAN, o: 0.55 },
    { r: 1.56, w: 0.025, arcs: [[0, 2.6], [3.0, 2.9]], speed: 0.22, color: CYAN, o: 0.7 },
    { r: 1.92, w: 0.07, arcs: [[0.5, 0.7], [1.4, 0.18], [1.7, 0.18], [2.0, 0.18], [3.3, 1.9]], speed: -0.15, color: DEEP, o: 0.75 },
    { r: 2.12, w: 0.01, arcs: [[0, Math.PI * 2]], speed: 0, color: STEEL, o: 0.25 },
  ];
  for (const spec of arcSpec) {
    const g = new THREE.Group();
    for (const [start, len] of spec.arcs) {
      g.add(new THREE.Mesh(new THREE.RingGeometry(spec.r - spec.w / 2, spec.r + spec.w / 2, Math.max(8, Math.ceil(len * 40)), 1, start, len), basic(spec.color, spec.o)));
    }
    body.add(g);
    arcRings.push({ g, spec });
  }

  // ---- the dial: 120 ticks, every tenth long
  const tickPts = [];
  for (let i = 0; i < 120; i++) {
    const a = (i / 120) * Math.PI * 2;
    const r0 = 2.24, r1 = i % 10 === 0 ? 2.42 : i % 5 === 0 ? 2.34 : 2.29;
    tickPts.push(new THREE.Vector3(Math.cos(a) * r0, Math.sin(a) * r0, 0), new THREE.Vector3(Math.cos(a) * r1, Math.sin(a) * r1, 0));
  }
  const dial = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(tickPts), lineMat(CYAN, 0.5));
  body.add(dial);

  // ---- the sweep: a fading wedge that scans while Jarvis thinks
  const sweep = new THREE.Mesh(
    new THREE.RingGeometry(0.9, 2.2, 48, 1, 0, 0.9),
    new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: { uColor: { value: CYAN.clone() }, uAlpha: { value: 0 } },
      vertexShader: `varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `uniform vec3 uColor; uniform float uAlpha; varying vec2 vP;
        void main(){ float a = atan(vP.y, vP.x); if (a < 0.0) a += 6.2831853; float f = smoothstep(0.9, 0.0, a);
          gl_FragColor = vec4(uColor, uAlpha * f * f * 0.45); }`,
    }),
  );
  body.add(sweep);

  // ---- the voice ring: radial bars that move with listening and speech
  const BARS = 96;
  const barGeo = new THREE.PlaneGeometry(0.022, 1);
  barGeo.translate(0, 0.5, 0);
  const bars = new THREE.InstancedMesh(barGeo, basic(CYAN, 0.9), BARS);
  const barSeed = Float32Array.from({ length: BARS }, () => Math.random());
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), v = new THREE.Vector3(), sc = new THREE.Vector3();
  body.add(bars);

  // ---- tilted orbit rings with riders: the 3D depth around the flat dial
  const orbits = [];
  const orbitSpec = [
    { r: 2.55, tilt: [1.25, 0.2, 0], speed: 0.3, color: CYAN, o: 0.45 },
    { r: 2.75, tilt: [1.1, -0.55, 0.4], speed: -0.2, color: STEEL, o: 0.25 },
    { r: 2.95, tilt: [0.75, -0.95, 0.3], speed: 0.13, color: WARM, o: 0.3 },
  ];
  for (const spec of orbitSpec) {
    const pivot = new THREE.Group();
    pivot.rotation.set(...spec.tilt);
    const spin = new THREE.Group();
    spin.add(new THREE.Mesh(new THREE.TorusGeometry(spec.r, 0.006, 6, 200), basic(spec.color, spec.o)));
    const rider = new THREE.Mesh(new THREE.SphereGeometry(0.04, 12, 8), new THREE.MeshBasicMaterial({ color: spec.color.clone().lerp(WHITE, 0.5) }));
    rider.position.x = spec.r;
    spin.add(rider);
    pivot.add(spin);
    rig.add(pivot);
    orbits.push({ spin, spec, pivot });
  }

  // ---- a shell of light (Fibonacci sphere), drawn after the bloom so it stays crisp
  const COUNT = 900;
  const pos = new Float32Array(COUNT * 3);
  const seed = new Float32Array(COUNT);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < COUNT; i++) {
    const y = 1 - (i / (COUNT - 1)) * 2;
    const rad = Math.sqrt(1 - y * y);
    pos.set([Math.cos(golden * i) * rad, y, Math.sin(golden * i) * rad], i * 3);
    seed[i] = Math.random();
  }
  const shellGeo = new THREE.BufferGeometry();
  shellGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  shellGeo.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  const shellMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 }, uRadius: { value: 3.3 }, uPulse: { value: 0 }, uSwirl: { value: 0 },
      uAssemble: { value: 0 }, uSize: { value: 1.5 }, uColor: { value: CYAN.clone() }, uPixel: { value: 1 },
    },
    vertexShader: `
      attribute float seed;
      uniform float uTime, uRadius, uPulse, uSwirl, uAssemble, uSize, uPixel;
      varying float vAlpha;
      void main() {
        vec3 p = normalize(position);
        float ang = uSwirl * (0.6 + seed) * uTime * 0.4 + (1.0 - uAssemble) * 3.0 * seed;
        float c = cos(ang), s = sin(ang);
        p = vec3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
        float r = uRadius * (1.0 + sin(uTime * 2.0 + seed * 6.2831) * 0.04 + uPulse * (0.1 + 0.1 * seed)) * mix(2.4, 1.0, uAssemble);
        vec4 mv = modelViewMatrix * vec4(p * r, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = uSize * uPixel * (0.6 + seed) * (11.0 / -mv.z);
        vAlpha = uAssemble * (0.3 + 0.7 * seed);
      }`,
    fragmentShader: `
      uniform vec3 uColor; varying float vAlpha;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        if (d > 0.5) discard;
        gl_FragColor = vec4(uColor * 1.15, 0.6 * vAlpha * smoothstep(0.5, 0.0, d));
      }`,
  });
  const shellScene = new THREE.Scene();
  const shellRig = new THREE.Group();
  shellScene.add(shellRig);
  shellRig.add(new THREE.Points(shellGeo, shellMat));

  // ---- bloom
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.75, 0.2, 0.32);
  composer.addPass(bloom);
  const sparks = new RenderPass(shellScene, camera);
  sparks.clear = false;
  composer.addPass(sparks);
  composer.addPass(new OutputPass());

  function resize() {
    const width = canvas.clientWidth, height = canvas.clientHeight;   // layout size: a CSS scale must not lower the resolution
    if (!width || !height) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false);
    composer.setPixelRatio(ratio);
    composer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    shellMat.uniforms.uPixel.value = ratio * Math.min(width, height) / 420;
    if (reduceMotion.matches) draw(performance.now());
  }
  new ResizeObserver(resize).observe(canvas);

  const look = { x: 0, y: 0, tx: 0, ty: 0 };
  if (parallax) {
    addEventListener("pointermove", (e) => {
      look.tx = (e.clientX / innerWidth - 0.5) * 0.45;
      look.ty = (e.clientY / innerHeight - 0.5) * 0.3;
    }, { passive: true });
  }

  const stateOf = () => { const s = getState(); return PROFILES[s] ? s : "idle"; };
  const live = { ...PROFILES.booting };
  const born = performance.now();
  let last = born, clock = 0;

  function draw(now) {
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    const state = stateOf();
    const target = PROFILES[state];
    const still = reduceMotion.matches;
    const k = 1 - Math.exp(-dt * 3);
    for (const key of Object.keys(live)) live[key] += (target[key] - live[key]) * (still ? 1 : k);
    clock += dt * live.spin;

    const t = (now - born) / 1000;
    const assemble = still ? 1 : Math.min(1, t / 1.8);
    const ease = 1 - Math.pow(1 - assemble, 3);

    // The voice plays through macOS `say`, so there is no audio level here: a lively envelope instead.
    let speak = 0;
    if (state === "speaking" && !still) speak = Math.max(0, Math.sin(t * 13) * 0.5 + Math.sin(t * 7.3) * 0.35 + Math.sin(t * 21.1) * 0.15);
    const beat = live.beat && !still ? Math.sin(t * live.beat * Math.PI) * 0.5 + 0.5 : 0;

    heart.scale.setScalar(ease * (1 + 0.15 * beat + 0.4 * speak));
    heart.material.color.copy(WHITE).multiplyScalar(1.2 + live.glow * 0.8 + speak * 1.2);
    halo.scale.setScalar(ease * (1 + 0.12 * beat + 0.3 * speak));
    halo.material.opacity = 0.2 + live.glow * 0.15 + speak * 0.25;
    globe.rotation.set(0.3, clock * 0.35, 0);
    globe.scale.setScalar(ease);
    globeMat.opacity = 0.3 + live.glow * 0.12;

    arcRings.forEach(({ g, spec }, i) => {
      g.rotation.z = clock * spec.speed;
      const d = Math.min(1, Math.max(0, (assemble - 0.08 * i) / 0.6));
      g.scale.setScalar(0.7 + 0.3 * (1 - Math.pow(1 - d, 3)));
      for (const m of g.children) m.material.opacity = spec.o * d * (0.75 + 0.25 * live.glow) + speak * 0.08;
    });
    dial.rotation.z = -clock * 0.04;
    dial.material.opacity = 0.45 * ease;

    sweep.rotation.z = -t * 3.2;
    sweep.material.uniforms.uAlpha.value += ((state === "thinking" ? 1 : 0) - sweep.material.uniforms.uAlpha.value) * k;

    for (let i = 0; i < BARS; i++) {
      const a = (i / BARS) * Math.PI * 2;
      const wave = Math.sin(t * 9 + i * 0.55 + barSeed[i] * 6) * 0.5 + 0.5;
      const ripple = Math.sin(a * 3 + t * 4) * 0.5 + 0.5;
      const len = 0.03 + live.voice * (0.06 + 0.32 * wave * (0.4 + 0.6 * ripple)) * (0.35 + speak * 1.1 + (state === "listening" ? 0.6 * beat : 0.3));
      v.set(Math.cos(a) * 0.86, Math.sin(a) * 0.86, 0.001);
      q.setFromAxisAngle(new THREE.Vector3(0, 0, 1), a - Math.PI / 2);
      sc.set(1, len * ease, 1);
      bars.setMatrixAt(i, m4.compose(v, q, sc));
    }
    bars.instanceMatrix.needsUpdate = true;

    orbits.forEach(({ spin, spec, pivot }, i) => {
      spin.rotation.z = clock * spec.speed * 2;
      const d = Math.min(1, Math.max(0, (assemble - 0.15 * (i + 1)) / 0.6));
      pivot.scale.setScalar(1 - Math.pow(1 - d, 3));
    });

    const u = shellMat.uniforms;
    u.uTime.value = t;
    u.uPulse.value = live.pulse * beat + speak * 0.8;
    u.uSwirl.value = live.swirl;
    u.uAssemble.value = ease;
    bloom.strength = (live.bloom + speak * 0.4) * glow;

    look.x += (look.tx - look.x) * k;
    look.y += (look.ty - look.y) * k;
    rig.rotation.y = look.x + (still ? 0 : Math.sin(t * 0.25) * 0.12);
    rig.rotation.x = look.y;
    shellRig.rotation.copy(rig.rotation);

    composer.render(dt);
  }

  // Draw only while the canvas is on screen and the window is visible.
  let raf = 0, visible = true;
  function loop(now) { draw(now); raf = requestAnimationFrame(loop); }
  function sync() {
    const run = visible && !document.hidden && !reduceMotion.matches;
    if (run && !raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
    if (!run && raf) { cancelAnimationFrame(raf); raf = 0; }
    if (!run) draw(performance.now());
  }
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); }).observe(canvas);
  document.addEventListener("visibilitychange", sync);
  reduceMotion.addEventListener("change", sync);

  resize();
  sync();
  return { redraw: () => setTimeout(() => draw(performance.now()), 50) };
}
