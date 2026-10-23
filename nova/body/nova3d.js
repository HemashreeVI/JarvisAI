// Nova in real 3D: a jointed robot with glossy plastic, reflections and shadows,
// plus Pip the robo-kitty. Every pose is computed procedurally each frame, so
// emotions blend smoothly and actions (spin, dance, crawl, walk...) can interrupt anything.
import * as THREE from 'three';
import { RoundedBoxGeometry } from './vendor/RoundedBoxGeometry.js';
import { RoomEnvironment } from './vendor/RoomEnvironment.js';
import { EYE_COLORS, EyeScreen, PetEyes } from './eyes.js';

const TAU = Math.PI * 2;
const params = new URLSearchParams(location.search);
const bridge = () => window.pywebview?.api;

// renderer / scene
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x000000, 0);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.75;

const camera = new THREE.PerspectiveCamera(27, 1, 0.1, 100);
const CAM_TARGET = new THREE.Vector3(-0.42, 1.55, 0);
camera.position.set(-0.42, 2.5, 11.2);
camera.lookAt(CAM_TARGET);

const key = new THREE.DirectionalLight(0xfff4ee, 2.4);
key.position.set(-3, 6, 5);
key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
Object.assign(key.shadow.camera, { left: -3, right: 3, top: 4, bottom: -1, near: 0.5, far: 20 });
key.shadow.radius = 4;
key.shadow.bias = -0.0005;
scene.add(key);
const rim = new THREE.DirectionalLight(0xb8e4ff, 1.6);
rim.position.set(4, 3, -4);
scene.add(rim);
scene.add(new THREE.HemisphereLight(0xffffff, 0xf3c9d0, 0.7));

const floor = new THREE.Mesh(new THREE.PlaneGeometry(14, 8), new THREE.ShadowMaterial({ opacity: 0.22 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

// Shaped-window mode (Windows): Python cuts the window to Nova's silhouette, so every pixel
// must be either fully opaque or fully transparent. We render to a texture, snap the alpha,
// and send the silhouette as rectangles a few times per second.
let shaped = false, rt = null, maskRT = null, outScene = null, maskScene = null, maskBuf = null, lastShape = '';
const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
const QUAD_VS = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }';

function enableShaped() {
  if (shaped) return;
  shaped = true;
  document.body.classList.add('shaped');
  rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
  maskRT = new THREE.WebGLRenderTarget(1, 1);
  const quad = mat => { const sc = new THREE.Scene(); sc.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat)); return sc; };
  outScene = quad(new THREE.ShaderMaterial({
    uniforms: { tDiffuse: { value: rt.texture } }, vertexShader: QUAD_VS, depthTest: false, depthWrite: false,
    fragmentShader: `uniform sampler2D tDiffuse; varying vec2 vUv;
      void main() {
        vec4 c = texture2D(tDiffuse, vUv);
        if (c.a < 0.5) { gl_FragColor = vec4(0.0); return; }
        gl_FragColor = vec4(c.rgb / c.a, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  }));
  maskScene = quad(new THREE.ShaderMaterial({
    uniforms: { tDiffuse: { value: rt.texture } }, vertexShader: QUAD_VS, depthTest: false, depthWrite: false, toneMapped: false,
    fragmentShader: `uniform sampler2D tDiffuse; varying vec2 vUv;
      void main() { gl_FragColor = vec4(texture2D(tDiffuse, vUv).a >= 0.5 ? 1.0 : 0.0, 0.0, 0.0, 1.0); }`,
  }));
  resize();
}

function render() {
  if (!shaped) { renderer.render(scene, camera); return; }
  renderer.setRenderTarget(rt);
  renderer.render(scene, camera);
  renderer.setRenderTarget(null);
  renderer.render(outScene, quadCam);
}

function silhouette() {
  // rows of opaque pixels, merged vertically into rectangles [x, y, w, h] in CSS px
  const w = maskRT.width, h = maskRT.height;
  renderer.setRenderTarget(maskRT);
  renderer.render(maskScene, quadCam);
  renderer.setRenderTarget(null);
  renderer.readRenderTargetPixels(maskRT, 0, 0, w, h, maskBuf);
  const rects = [];
  let open = [], prevKey = '';
  for (let y = 0; y < h; y++) {
    const row = (h - 1 - y) * w * 4, spans = [];
    for (let x = 0; x < w; x++) {
      if (maskBuf[row + x * 4] > 127) {
        const x0 = x;
        while (x < w && maskBuf[row + x * 4] > 127) x++;
        spans.push([x0, x - x0]);
      }
    }
    const key = spans.join(';');
    if (key === prevKey) { open.forEach(r => r[3]++); continue; }
    rects.push(...open);
    open = spans.map(([x0, len]) => [x0, y, len, 1]);
    prevKey = key;
  }
  rects.push(...open);
  for (const [el, extra] of [[bubble, 14], [menu, 2], [input, 2]]) { // UI on top of Nova
    if (!el.classList.contains('show')) continue;
    const r = el.getBoundingClientRect();
    rects.push([Math.floor(r.left) - 1, Math.floor(r.top) - 1, Math.ceil(r.width) + 2, Math.ceil(r.height) + extra]);
  }
  return rects;
}

let shapeClock = 0;
function updateShape(dt) {
  if (!shaped || !bridge()?.set_shape) return;
  shapeClock += dt;
  if (shapeClock < 0.08) return;
  shapeClock = 0;
  const rects = silhouette(), key = JSON.stringify(rects);
  if (key === lastShape) return;
  lastShape = key;
  bridge().set_shape(rects, window.devicePixelRatio || 1);
}

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (shaped) {
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    rt.setSize(size.x, size.y);
    maskRT.setSize(w, h);
    maskBuf = new Uint8Array(w * h * 4);
    lastShape = '';
  }
}
window.addEventListener('resize', resize);
resize();

// materials
const plastic = new THREE.MeshPhysicalMaterial({ color: 0xf4f1f3, roughness: 0.36, clearcoat: 0.8, clearcoatRoughness: 0.18, sheen: 0.3, sheenColor: 0xffe6ee });
const joint = new THREE.MeshPhysicalMaterial({ color: 0x23262f, roughness: 0.45, metalness: 0.35, clearcoat: 0.4 });
const rubber = new THREE.MeshStandardMaterial({ color: 0x15171d, roughness: 0.7 });
const pinkInner = new THREE.MeshStandardMaterial({ color: 0xffb3c8, roughness: 0.6 });

function glassWithScreen(texture, repeatW, repeatH) {
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.repeat.set(1 / repeatW, 1 / repeatH);
  texture.offset.set(0.5, 0.5);
  const front = new THREE.MeshPhysicalMaterial({
    color: 0x07080d, roughness: 0.12, metalness: 0.1, clearcoat: 1, clearcoatRoughness: 0.05,
    emissive: 0xffffff, emissiveMap: texture, emissiveIntensity: 1.55, envMapIntensity: 0.45,
  });
  const edge = new THREE.MeshPhysicalMaterial({ color: 0x0b0d14, roughness: 0.2, clearcoat: 1 });
  return [front, edge];
}

function roundedRectShape(w, h, r) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y);
  return s;
}

function mesh(geo, mat, parent, pos = [0, 0, 0], rot = [0, 0, 0]) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(...pos); m.rotation.set(...rot);
  m.castShadow = true; m.receiveShadow = true;
  parent.add(m);
  return m;
}
function group(parent, pos = [0, 0, 0]) { const g = new THREE.Group(); g.position.set(...pos); parent.add(g); return g; }

// Nova
const eyes = new EyeScreen();
const eyeTex = new THREE.CanvasTexture(eyes.canvas);
const VISOR_W = 1.26, VISOR_H = 0.86;
const HIP_HEIGHT = 0.64;

function buildNova() {
  const root = group(scene);
  const hips = group(root, [0, HIP_HEIGHT, 0]);
  const R = { root, hips, shoulder: {}, elbow: {}, hip: {}, knee: {} };

  // torso + chest light
  mesh(new RoundedBoxGeometry(0.8, 0.66, 0.56, 5, 0.2), plastic, hips, [0, 0.36, 0]);
  mesh(new RoundedBoxGeometry(0.36, 0.3, 0.06, 3, 0.03), joint, hips, [0, 0.39, 0.27]);
  R.chestMat = new THREE.MeshStandardMaterial({ color: 0x000000, emissive: 0x5ef0ff, emissiveIntensity: 2 });
  mesh(new THREE.SphereGeometry(0.055, 20, 12), R.chestMat, hips, [0, 0.39, 0.31]);
  mesh(new THREE.CylinderGeometry(0.33, 0.36, 0.12, 28), joint, hips, [0, 0.02, 0]); // waist ring

  // neck + head
  R.neck = group(hips, [0, 0.7, 0]);
  mesh(new THREE.CylinderGeometry(0.15, 0.18, 0.16, 20), joint, R.neck, [0, 0.04, 0]);
  R.head = group(R.neck, [0, 0.1, 0]);
  const headMesh = mesh(new RoundedBoxGeometry(1.6, 1.2, 1.18, 7, 0.44), plastic, R.head, [0, 0.6, 0]);
  // top vents
  for (const [i, dx] of [[0, 0.28], [1, 0.36]]) mesh(new THREE.CapsuleGeometry(0.018, 0.2, 4, 8), joint, R.head, [dx, 1.17 - i * 0.03, -0.2 + i * 0.1], [Math.PI / 2, 0, 0.25]);
  // visor with the glowing eye screen
  const visorGeo = new THREE.ExtrudeGeometry(roundedRectShape(VISOR_W, VISOR_H, 0.3), { depth: 0.04, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 5, curveSegments: 18 });
  R.visorMats = glassWithScreen(eyeTex, VISOR_W, VISOR_H);
  mesh(visorGeo, R.visorMats, R.head, [0, 0.63, 0.555]);
  // ear pods
  R.earMats = [];
  for (const side of [-1, 1]) {
    const ear = group(R.head, [side * 0.82, 0.62, 0]);
    mesh(new THREE.CylinderGeometry(0.26, 0.26, 0.16, 32), plastic, ear, [0, 0, 0], [0, 0, Math.PI / 2]);
    mesh(new THREE.TorusGeometry(0.15, 0.045, 12, 32), joint, ear, [side * 0.085, 0, 0], [0, Math.PI / 2, 0]);
    const m = new THREE.MeshStandardMaterial({ color: 0x000000, emissive: 0x5ef0ff, emissiveIntensity: 1.2 });
    R.earMats.push(m);
    mesh(new THREE.CircleGeometry(0.09, 24), m, ear, [side * 0.082, 0, 0], [0, side * Math.PI / 2, 0]);
  }
  R.headMesh = headMesh;

  // arms: shoulder -> upper arm -> elbow -> forearm -> hand
  for (const side of [-1, 1]) {
    const k = side < 0 ? 'L' : 'R';
    const sh = group(hips, [side * 0.49, 0.56, 0]);
    mesh(new THREE.SphereGeometry(0.13, 20, 14), plastic, sh);
    mesh(new THREE.CapsuleGeometry(0.1, 0.24, 6, 14), plastic, sh, [0, -0.2, 0]);
    const el = group(sh, [0, -0.4, 0]);
    mesh(new THREE.SphereGeometry(0.085, 16, 12), joint, el);
    mesh(new THREE.CapsuleGeometry(0.095, 0.2, 6, 14), plastic, el, [0, -0.17, 0]);
    const hand = group(el, [0, -0.36, 0]);
    mesh(new THREE.SphereGeometry(0.1, 18, 14), joint, hand);
    for (const f of [-1, 0, 1]) mesh(new THREE.CapsuleGeometry(0.026, 0.07, 4, 8), joint, hand, [f * 0.045, -0.09, 0.04], [0.3, 0, f * 0.15]);
    R.shoulder[k] = sh; R.elbow[k] = el;
  }
  // legs: hip -> thigh -> knee -> boot
  for (const side of [-1, 1]) {
    const k = side < 0 ? 'L' : 'R';
    const hp = group(hips, [side * 0.18, 0, 0]);
    mesh(new THREE.CapsuleGeometry(0.085, 0.16, 6, 12), joint, hp, [0, -0.14, 0]);
    const kn = group(hp, [0, -0.29, 0]);
    mesh(new THREE.SphereGeometry(0.09, 14, 10), joint, kn);
    mesh(new RoundedBoxGeometry(0.24, 0.3, 0.3, 4, 0.1), plastic, kn, [0, -0.17, 0.02]);
    mesh(new RoundedBoxGeometry(0.25, 0.06, 0.32, 3, 0.025), rubber, kn, [0, -0.32, 0.03]);
    R.hip[k] = hp; R.knee[k] = kn;
  }
  root.traverse(o => { if (o.isMesh) o.userData.who = 'nova'; });
  return R;
}

// Pip the robo-kitty
const petEyes = new PetEyes();
const petTex = new THREE.CanvasTexture(petEyes.canvas);

function buildPip() {
  const root = group(scene, [-1.5, 0, 0.35]);
  const body = group(root, [0, 0.3, 0]);
  const P = { root, body, legs: [] };
  mesh(new THREE.SphereGeometry(0.27, 28, 20), plastic, body).scale.set(1, 0.88, 1.18);
  P.head = group(body, [0, 0.3, 0.2]);
  mesh(new THREE.SphereGeometry(0.27, 30, 22), plastic, P.head).scale.set(1.12, 0.95, 1);
  for (const side of [-1, 1]) {
    const ear = group(P.head, [side * 0.16, 0.2, -0.02]);
    ear.rotation.z = -side * 0.35;
    mesh(new THREE.ConeGeometry(0.09, 0.2, 16), plastic, ear, [0, 0.07, 0]);
    mesh(new THREE.ConeGeometry(0.05, 0.12, 12), pinkInner, ear, [0, 0.06, 0.04]);
    (P.ears ||= []).push(ear);
  }
  const faceGeo = new THREE.ExtrudeGeometry(roundedRectShape(0.38, 0.22, 0.1), { depth: 0.02, bevelEnabled: true, bevelThickness: 0.015, bevelSize: 0.015, bevelSegments: 4 });
  mesh(faceGeo, glassWithScreen(petTex, 0.38, 0.22), P.head, [0, -0.01, 0.24]);
  for (const [x, z] of [[-0.14, 0.17], [0.14, 0.17], [-0.14, -0.17], [0.14, -0.17]]) {
    const leg = group(body, [x, -0.12, z]);
    mesh(new THREE.CapsuleGeometry(0.06, 0.1, 4, 10), plastic, leg, [0, -0.08, 0]);
    P.legs.push(leg);
  }
  P.tail = group(body, [0, 0.05, -0.3]);
  const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0.1, -0.12), new THREE.Vector3(0, 0.3, -0.16), new THREE.Vector3(0.05, 0.42, -0.08)]);
  mesh(new THREE.TubeGeometry(curve, 24, 0.04, 10), plastic, P.tail);
  root.traverse(o => { if (o.isMesh) o.userData.who = 'pip'; });
  return P;
}

const nova = buildNova();
const pip = buildPip();

// state
const S = {
  emotion: 'neutral', emotionUntil: 0, activity: 'idle', expr: 'neutral', exprSince: 0,
  color: new THREE.Color(EYE_COLORS.neutral), lastInteraction: performance.now() / 1000,
  nextBlink: 2, blinkStart: -1, gaze: [0, 0], pointer: [0, 0], pointerAt: 0,
  actions: [], action: null, petHappyUntil: 0, dir: 1, bubbleVisible: false,
};
const now = () => performance.now() / 1000;

// poses. Shoulder: [forward(-)/back(+), out]; elbow: bend; hip: forward(-); knee: bend
function rest() {
  return { y: 0, x: 0, yaw: 0, lean: 0, roll: 0, headX: 0, headY: 0, headZ: 0,
    shLx: 0, shLz: 0.12, shRx: 0, shRz: 0.12, elL: 0.15, elR: 0.15, hipL: 0, hipR: 0, knL: 0, knR: 0, petX: -1.5, petHop: 0 };
}

function expressionPose(e, t) {
  const p = rest();
  const breathe = Math.sin(t * 2) * 0.012;
  p.y = breathe;
  p.headY = S.gaze[0] * 0.05; p.headX = S.gaze[1] * 0.03;
  switch (e) {
    case 'happy':
      p.y = Math.abs(Math.sin(t * 4)) * 0.06; p.roll = Math.sin(t * 2) * 0.05; p.headZ = Math.sin(t * 2) * 0.1;
      p.shRz = 2.5 + Math.sin(t * 9) * 0.3; p.elR = 0.4 + Math.sin(t * 9) * 0.3; // waving
      break;
    case 'excited':
      p.y = Math.abs(Math.sin(t * 6)) * 0.22; p.knL = p.knR = 0.25 * (1 - Math.abs(Math.sin(t * 6)));
      p.shLz = 2.6 + Math.sin(t * 12) * 0.25; p.shRz = 2.6 - Math.sin(t * 12) * 0.25; p.elL = p.elR = 0.3;
      p.headZ = Math.sin(t * 6) * 0.08;
      break;
    case 'cute':
    case 'love':
      p.headZ = 0.22 + Math.sin(t * 1.5) * 0.08; p.roll = Math.sin(t * 1.5) * 0.07; p.lean = 0.06;
      p.shLx = p.shRx = -0.75; p.shLz = p.shRz = -0.28; p.elL = p.elR = 1.15; // hands together, shy
      p.hipL = -0.05; p.knL = 0.12; p.y = e === 'love' ? Math.abs(Math.sin(t * 2.4)) * 0.04 : breathe;
      break;
    case 'angry': {
      const stomp = Math.max(0, Math.sin(t * 5)) ** 3;
      p.hipR = -0.35 * stomp; p.knR = 0.5 * stomp; // stomping foot
      p.shLz = p.shRz = 0.7; p.shLx = p.shRx = 0.35; p.elL = p.elR = 1.6; // hands on hips
      p.headY = 0.42; p.headX = -0.12; p.headZ = -0.05; // "hmph!" looking away
      p.x = (Math.random() - 0.5) * 0.012;
      break;
    }
    case 'caring':
      p.headZ = 0.15; p.headX = 0.06; p.shLx = p.shRx = -0.5; p.shLz = p.shRz = 0.45; p.elL = p.elR = 0.4; // open arms
      break;
    case 'sad':
      p.headX = 0.35; p.lean = 0.1; p.shLz = p.shRz = 0.04; p.elL = p.elR = 0.05; p.y = -0.02 + Math.sin(t) * 0.006;
      break;
    case 'concerned':
      p.headZ = -0.12; p.shLx = p.shRx = -0.6; p.shLz = p.shRz = -0.15; p.elL = p.elR = 1.5;
      break;
    case 'sleepy': // sits down and nods off
      p.y = -0.42; p.hipL = p.hipR = -1.45; p.knL = p.knR = 0.15; p.lean = -0.08;
      p.headX = 0.2 + 0.18 * (0.5 + 0.5 * Math.sin(t * 0.9)); p.headZ = 0.12;
      p.shLz = p.shRz = 0.25; p.shLx = p.shRx = -0.3; p.elL = p.elR = 0.3;
      break;
    case 'surprised': {
      const k = Math.max(0, 1 - S.since / 0.4);
      p.y = Math.sin(Math.min(1, S.since / 0.4) * Math.PI) * 0.3; p.lean = -0.05;
      p.shLz = p.shRz = 1.3 + k * 0.5; p.elL = p.elR = 0.5; p.headX = 0.02;
      break;
    }
    case 'thinking':
      p.headZ = 0.18; p.headX = -0.15; p.headY = 0.12;
      p.shRx = -0.7; p.shRz = -0.32; p.elR = 2.4; // hand on chin
      p.shLx = -0.3; p.shLz = -0.35; p.elL = 1.3;
      break;
  }
  if (S.activity === 'listening') { p.headZ += 0.18; p.headX -= 0.05; }
  if (S.activity === 'speaking') { p.headX += Math.sin(t * 7) * 0.03; p.headY += Math.sin(t * 3) * 0.05; p.elR += Math.abs(Math.sin(t * 3)) * 0.3; }
  return p;
}

// actions: things Nova does with her whole body
const ease = k => k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
const ACTIONS = {
  spin: { dur: 1.5, pose(p, k) {
    p.yaw = TAU * ease(k); p.y = Math.sin(k * Math.PI) * 0.45; p.shLz = p.shRz = 1.4; p.elL = p.elR = 0.2;
    p.knL = p.knR = 0.3 * Math.sin(k * Math.PI); p.headX = -0.1;
  } },
  jump: { dur: 1.1, pose(p, k) {
    if (k < 0.25) { const c = k / 0.25; p.y = -0.14 * c; p.knL = p.knR = 0.7 * c; p.hipL = p.hipR = -0.35 * c; p.shLx = p.shRx = 0.5 * c; }
    else if (k < 0.8) { const a = (k - 0.25) / 0.55; p.y = Math.sin(a * Math.PI) * 0.75; p.shLz = p.shRz = 2.7; p.knL = p.knR = 0.4 * Math.sin(a * Math.PI); }
    else { const c = 1 - (k - 0.8) / 0.2; p.y = -0.1 * c; p.knL = p.knR = 0.5 * c; }
  } },
  wave: { dur: 2.6, pose(p, k, t) {
    p.shRz = 2.55 + Math.sin(t * 11) * 0.28; p.elR = 0.5 + Math.sin(t * 11) * 0.35; p.headZ = 0.15; p.roll = 0.04;
  } },
  dance: { dur: 6.5, pose(p, k, t) {
    const b = t * 2.3 * Math.PI;
    p.x = Math.sin(b / 2) * 0.28; p.roll = Math.sin(b / 2) * 0.12; p.y = Math.abs(Math.sin(b)) * 0.1;
    p.shLz = 1.4 + Math.sin(b) * 1.1; p.shRz = 1.4 - Math.sin(b) * 1.1; p.elL = p.elR = 0.6;
    p.hipL = Math.min(0, Math.sin(b)) * 0.45; p.hipR = Math.min(0, -Math.sin(b)) * 0.45;
    p.knL = -p.hipL * 1.2; p.knR = -p.hipR * 1.2; p.headZ = Math.sin(b / 2) * 0.22; p.yaw = Math.sin(b / 4) * 0.5;
    if (k > 0.82) p.yaw = TAU * ease((k - 0.82) / 0.18);
  } },
  walk: { dur: 5.5, move: 1.0, pose(p, k, t) {
    const c = t * 2 * TAU, sw = Math.sin(c);
    p.yaw = S.dir * Math.PI / 2; p.headY = -S.dir * 0.75; // walks sideways, looks at you
    p.x = -0.42 - S.dir * 0.3;
    p.hipL = sw * 0.5; p.hipR = -sw * 0.5; p.knL = Math.max(0, -Math.sin(c + 0.6)) * 0.7; p.knR = Math.max(0, Math.sin(c + 0.6)) * 0.7;
    p.shLx = -sw * 0.55; p.shRx = sw * 0.55; p.elL = p.elR = 0.35; p.y = Math.abs(Math.cos(c)) * 0.05; p.lean = 0.06;
  } },
  crawl: { dur: 7, move: 0.45, pose(p, k, t) {
    const c = t * 1.6 * TAU, sw = Math.sin(c);
    const inK = Math.min(1, k / 0.12, (1 - k) / 0.12); // get down, crawl, stand back up
    const L = 1.25 * inK;
    p.yaw = S.dir * Math.PI / 2; p.headY = -S.dir * 0.85 * inK;
    p.x = (-0.42 - S.dir * 0.55) * inK; // keep her big head inside the window
    p.lean = L; p.y = -0.33 * inK + Math.abs(Math.cos(c)) * 0.025 * inK;
    p.headX = -L * 0.75; p.headZ = Math.sin(c / 2) * 0.1 * inK;
    p.shLx = -L - 0.25 * inK + sw * 0.35 * inK; p.shRx = -L - 0.25 * inK - sw * 0.35 * inK; p.shLz = p.shRz = 0.08;
    p.elL = 0.25 * inK + Math.max(0, sw) * 0.3 * inK; p.elR = 0.25 * inK + Math.max(0, -sw) * 0.3 * inK;
    p.hipL = -L + 0.05 - sw * 0.25 * inK; p.hipR = -L + 0.05 + sw * 0.25 * inK;
    p.knL = 1.5 * inK + Math.max(0, -sw) * 0.25 * inK; p.knR = 1.5 * inK + Math.max(0, sw) * 0.25 * inK;
  } },
  hug: { dur: 3, pose(p, k, t) {
    const open = k < 0.4 ? k / 0.4 : 1, close = k > 0.55 ? Math.min(1, (k - 0.55) / 0.2) : 0;
    p.shLx = p.shRx = -1.2 * open; p.shLz = p.shRz = 1.2 * open - 1.25 * close; p.elL = p.elR = 0.3 + 0.9 * close;
    p.headZ = 0.2; p.lean = 0.12 * close; p.roll = Math.sin(t * 3) * 0.05 * close;
  } },
};
const ROUTINES = {
  cheer: ['wave', 'spin', 'dance', 'crawl', 'spin', 'hug'],
  party: ['spin', 'dance', 'jump', 'spin'],
};

function perform(name) {
  const list = ROUTINES[name] || (ACTIONS[name] ? [name] : []);
  if (!list.length) return false;
  S.actions = [...list];
  S.action = null;
  touch();
  return true;
}

// particles: hearts, sparkles, z, steam, ! ?
function spriteTexture(draw, size = 128) {
  const c = document.createElement('canvas'); c.width = c.height = size;
  draw(c.getContext('2d'), size);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; return tex;
}
const TEX = {
  heart: spriteTexture((c, s) => { c.fillStyle = '#ff5c9d'; c.shadowColor = '#ff5c9d'; c.shadowBlur = 14; c.beginPath();
    for (let i = 0; i <= 40; i++) { const t = TAU * i / 40, x = 16 * Math.sin(t) ** 3, y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t); c.lineTo(s / 2 + x * 2.6, s / 2 - y * 2.6); } c.fill(); }),
  sparkle: spriteTexture((c, s) => { c.fillStyle = '#fff6a6'; c.shadowColor = '#ffe96b'; c.shadowBlur = 16; c.beginPath();
    for (let i = 0; i < 8; i++) { const r = i % 2 ? s * 0.1 : s * 0.42, a = Math.PI / 4 * i - Math.PI / 2; c.lineTo(s / 2 + r * Math.cos(a), s / 2 + r * Math.sin(a)); } c.fill(); }),
  z: spriteTexture((c, s) => { c.fillStyle = '#9fd0ee'; c.font = `bold ${s * 0.8}px Arial`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('z', s / 2, s / 2); }),
  steam: spriteTexture((c, s) => { const g = c.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2); g.addColorStop(0, 'rgba(255,255,255,0.95)'); g.addColorStop(1, 'rgba(255,255,255,0)'); c.fillStyle = g; c.fillRect(0, 0, s, s); }),
  bang: spriteTexture((c, s) => { c.fillStyle = '#ffcf3f'; c.font = `900 ${s * 0.9}px Arial`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('!', s / 2, s / 2); }),
  question: spriteTexture((c, s) => { c.fillStyle = '#aab5ff'; c.font = `900 ${s * 0.8}px Arial`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('?', s / 2, s / 2); }),
  anger: spriteTexture((c, s) => { c.strokeStyle = '#ff2a2a'; c.lineWidth = s * 0.1; c.lineCap = 'round'; const d = s * 0.17;
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) { const ox = s / 2 + sx * d * 1.6, oy = s / 2 + sy * d * 1.6, a = Math.atan2(-sy, -sx); c.beginPath(); c.arc(ox, oy, d * 1.15, a - Math.PI / 4, a + Math.PI / 4); c.stroke(); } }),
};
const particles = [];
function spawn(kind, pos, { vel = [0, 0.5, 0], life = 1.6, size = 0.22, gravity = 0 } = {}) {
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: TEX[kind], transparent: true, depthWrite: false }));
  sp.position.copy(pos); sp.scale.setScalar(size);
  scene.add(sp);
  particles.push({ sp, vel: new THREE.Vector3(...vel), born: now(), life, size, gravity, kind });
}
const angerMark = new THREE.Sprite(new THREE.SpriteMaterial({ map: TEX.anger, transparent: true, depthWrite: false }));
angerMark.scale.setScalar(0.32); angerMark.position.set(0.62, 1.22, 0.45); nova.head.add(angerMark);

let spawnClock = 0;
function updateParticles(dt, t) {
  spawnClock += dt;
  const e = S.expr, head = new THREE.Vector3();
  nova.head.getWorldPosition(head);
  if (spawnClock > 0.4) {
    spawnClock = 0;
    const r = Math.random(), rnd = (a, b) => a + Math.random() * (b - a);
    if (e === 'love' && r < 0.7) spawn('heart', head.clone().add(new THREE.Vector3(rnd(-0.8, 0.8), 1.2, 0.4)), { vel: [rnd(-0.1, 0.1), 0.45, 0], size: rnd(0.16, 0.26) });
    else if (e === 'cute' && r < 0.35) spawn('heart', head.clone().add(new THREE.Vector3(rnd(-0.9, 0.9), 1.1, 0.4)), { size: 0.15 });
    else if (e === 'excited' && r < 0.8) spawn('sparkle', head.clone().add(new THREE.Vector3(rnd(-1.1, 1.1), rnd(0, 1.3), 0.6)), { vel: [0, 0, 0], life: 0.8, size: rnd(0.12, 0.24) });
    else if (e === 'sleepy' && r < 0.4) spawn('z', head.clone().add(new THREE.Vector3(0.7, 1.1, 0.3)), { vel: [0.18, 0.3, 0], life: 2.6, size: rnd(0.15, 0.25) });
    else if (e === 'angry' && r < 0.55) { const s = Math.random() < 0.5 ? -1 : 1; spawn('steam', head.clone().add(new THREE.Vector3(s * 1.0, 0.75, 0)), { vel: [s * 0.25, 0.6, 0], life: 1, size: rnd(0.15, 0.25) }); }
    else if (e === 'thinking' && r < 0.2) spawn('question', pip.root.position.clone().add(new THREE.Vector3(0.2, 1.05, 0)), { vel: [0, 0.15, 0], life: 1.4, size: 0.22 });
  }
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i], k = (now() - p.born) / p.life;
    if (k >= 1) { scene.remove(p.sp); p.sp.material.dispose(); particles.splice(i, 1); continue; }
    p.vel.y -= p.gravity * dt;
    p.sp.position.addScaledVector(p.vel, dt);
    p.sp.material.opacity = k < 0.8 ? 1 : (1 - k) / 0.2;
    const grow = p.kind === 'steam' ? 1 + k * 1.5 : p.kind === 'sparkle' ? Math.sin(k * Math.PI) : 1;
    p.sp.scale.setScalar(p.size * grow);
  }
  angerMark.visible = e === 'angry';
  angerMark.scale.setScalar(0.3 * (1 + 0.15 * Math.sin(t * 10)));
}

// animation loop
let cur = rest();
let last = now();
const tmpColor = new THREE.Color();

function exprNow(t) {
  if (t > S.emotionUntil && S.emotion !== 'neutral' && S.activity === 'idle' && !S.action) S.emotion = 'neutral';
  let e = S.emotion;
  if (S.activity === 'thinking') e = 'thinking';
  else if (S.activity === 'idle' && e === 'neutral' && !S.bubbleVisible && !S.action && t - S.lastInteraction > 240) e = 'sleepy';
  if (S.action && (S.action.name === 'spin' || S.action.name === 'dance') && e === 'neutral') e = 'happy';
  return e;
}

function openness(t) {
  const blinks = !['happy', 'love', 'sleepy'].includes(S.expr);
  if (t > S.nextBlink && blinks) {
    S.blinkStart = t; S.nextBlink = t + 2.2 + Math.random() * 3.8;
    if (Math.random() < 0.2) S.nextBlink = t + 0.35;
  }
  const ph = (t - S.blinkStart) / 0.17;
  return ph >= 1 || ph < 0 ? 1 : Math.max(0.08, 1 - Math.sin(Math.PI * ph));
}

function apply(p) {
  const R = nova;
  R.root.position.x = p.x; R.root.rotation.y = p.yaw;
  R.hips.position.y = HIP_HEIGHT + p.y;
  R.hips.rotation.set(p.lean, 0, p.roll);
  R.head.rotation.set(p.headX, p.headY, p.headZ);
  R.shoulder.L.rotation.set(p.shLx, 0, -p.shLz); R.shoulder.R.rotation.set(p.shRx, 0, p.shRz);
  R.elbow.L.rotation.x = -p.elL; R.elbow.R.rotation.x = -p.elR;
  R.hip.L.rotation.x = p.hipL; R.hip.R.rotation.x = p.hipR;
  R.knee.L.rotation.x = p.knL; R.knee.R.rotation.x = p.knR;
}

function petMood(t) {
  if (t < S.petHappyUntil) return 'petted';
  return { happy: 'happy', excited: 'happy', cute: 'love', love: 'love', angry: 'scared', sad: 'sad', caring: 'sad',
    sleepy: 'sleepy', surprised: 'surprised', thinking: 'curious', concerned: 'sad' }[S.expr] || 'idle';
}

function animatePip(t, dt) {
  const mood = petMood(t), P = pip, a = S.action;
  const moving = a && ACTIONS[a.name].move;
  const tx = moving ? -0.42 - S.dir * 1.85 : { scared: -1.85, sad: -1.15 }[mood] ?? -1.5; // follows behind Nova
  const tz = moving ? -0.5 : 0.35;
  P.root.position.x += (tx - P.root.position.x) * Math.min(1, dt * 3);
  P.root.position.z += (tz - P.root.position.z) * Math.min(1, dt * 3);
  let hop = 0, yaw = 0.35, lie = 0;
  if (moving) { yaw = S.dir * Math.PI / 2; hop = Math.abs(Math.sin(t * 9)) * 0.06; }
  else if (mood === 'happy' || mood === 'petted') hop = Math.abs(Math.sin(t * (mood === 'happy' ? 7 : 9))) * 0.2;
  else if (mood === 'surprised') hop = Math.sin(Math.min(1, S.since / 0.4) * Math.PI) * 0.35;
  else if (mood === 'sleepy') lie = 1;
  else hop = 0.05 + Math.sin(t * 2.4 + 1.3) * 0.04;
  if (mood === 'petted') yaw = Math.sin(t * 6) * 0.5;
  if (a?.name === 'spin' || a?.name === 'dance') yaw = t * 6; // Pip spins along
  P.root.rotation.y += (yaw - P.root.rotation.y) * Math.min(1, dt * 6);
  P.body.position.y = 0.3 + hop - lie * 0.1;
  P.body.rotation.z = mood === 'scared' ? (Math.random() - 0.5) * 0.08 : 0;
  P.head.rotation.x = mood === 'curious' ? -0.3 : mood === 'sad' ? 0.25 : lie * 0.3;
  P.head.rotation.z = mood === 'love' ? 0.2 : 0;
  P.tail.rotation.y = Math.sin(t * (['happy', 'petted', 'love'].includes(mood) ? 12 : 2)) * 0.5;
  P.legs.forEach((leg, i) => { leg.rotation.x = moving ? Math.sin(t * 14 + (i % 3 ? Math.PI : 0)) * 0.6 : lie * -1.2 * (i < 2 ? 1 : -1); });
  P.ears.forEach((ear, i) => { ear.rotation.x = ['scared', 'sad', 'sleepy'].includes(mood) ? -0.7 : 0; });
  const col = { love: '#ff4f9a', scared: '#ffd36b', sad: '#8cc4ff', sleepy: '#4b9fbc' }[mood] || '#5ef0ff';
  petEyes.draw(mood, t, col, mood === 'idle' && Math.sin(t * 0.5) > 0.6 ? 1 : mood === 'curious' ? 0.5 : 0);
  petTex.needsUpdate = true;
  if (mood === 'love' && Math.random() < dt * 1.5) spawn('heart', P.root.position.clone().add(new THREE.Vector3(0, 0.95, 0)), { size: 0.12, vel: [0, 0.35, 0] });
}

async function stepMovement(speed, dt) {
  const api = bridge();
  if (!api?.nudge) return;
  const pxPerUnit = window.innerHeight / 4.4;
  try {
    const r = await api.nudge(S.dir * speed * pxPerUnit * dt);
    if (r && r.hit_edge) S.dir *= -1; // turn around at the screen edge
  } catch (_) { /* window closing */ }
}

function frame() {
  const t = now(), dt = Math.min(0.05, t - last); last = t;

  // actions queue
  if (!S.action && S.actions.length) {
    const name = S.actions.shift();
    S.action = { name, start: t };
    if (ACTIONS[name].move && Math.random() < 0.5) S.dir *= -1;
  }
  const e = exprNow(t);
  if (e !== S.expr) { S.exprSince = t; S.blinkStart = t; if (e === 'surprised' || S.expr === 'sleepy') spawn('bang', new THREE.Vector3(0.8, 3.1, 0.5), { vel: [0, 0.3, 0], life: 1, size: 0.4 }); }
  S.expr = e; S.since = t - S.exprSince;

  let target = expressionPose(e, t), k = 1 - Math.exp(-dt * 9);
  if (S.action) {
    const A = ACTIONS[S.action.name], ak = (t - S.action.start) / A.dur;
    if (ak >= 1) {
      cur.yaw = ((cur.yaw % TAU) + TAU + Math.PI) % TAU - Math.PI; // unwind spins without spinning back
      S.action = null;
    } else {
      A.pose(target, ak, t - S.action.start);
      k = 1 - Math.exp(-dt * 16);
      if (A.move) stepMovement(A.move, dt);
    }
  }
  for (const key in target) cur[key] += (target[key] - cur[key]) * k;
  apply(cur);

  // gaze follows the pointer (anywhere on screen when Python sends it)
  const g = S.expr === 'thinking' ? [7, -6] : ['sleepy', 'sad'].includes(S.expr) ? [0, 3] : [S.pointer[0] * 7, S.pointer[1] * 5];
  S.gaze[0] += (g[0] - S.gaze[0]) * Math.min(1, dt * 10);
  S.gaze[1] += (g[1] - S.gaze[1]) * Math.min(1, dt * 10);

  // eye screen + glow colours
  tmpColor.set(EYE_COLORS[e]);
  S.color.lerp(tmpColor, Math.min(1, dt * 8));
  const hex = '#' + S.color.getHexString();
  eyes.draw({ expr: e, color: hex, t, open: openness(t), gaze: S.gaze, speaking: S.activity === 'speaking', listening: S.activity === 'listening', since: S.since });
  eyeTex.needsUpdate = true;
  nova.visorMats[0].color.set(e === 'angry' ? 0x2a0408 : 0x07080d);
  nova.chestMat.emissive.copy(S.color);
  nova.chestMat.emissiveIntensity = 1.2 + Math.sin(t * (e === 'angry' || e === 'excited' ? 8 : 2.5)) * 0.8;
  const listening = S.activity === 'listening';
  nova.earMats.forEach(m => { m.emissive.copy(S.color); m.emissiveIntensity = listening ? 1.5 + Math.sin(t * 10) * 1.2 : 1.0; });

  animatePip(t, dt);
  updateParticles(dt, t);
  render();
  updateShape(dt);
  requestAnimationFrame(frame);
}

// UI: bubble, typing box, menu, mouse
const bubble = document.getElementById('bubble');
const input = document.getElementById('input');
const menu = document.getElementById('menu');
let bubbleTimer = null;

function say(text, emotion, seconds) {
  if (emotion) setEmotion(emotion, 40);
  bubble.textContent = text;
  bubble.style.setProperty('--accent', EYE_COLORS[emotion || S.emotion] || EYE_COLORS.neutral);
  bubble.classList.add('show');
  S.bubbleVisible = true;
  clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => { bubble.classList.remove('show'); S.bubbleVisible = false; }, (seconds || Math.min(14, 3 + text.length / 14)) * 1000);
}
function setEmotion(e, hold = 40) { S.emotion = EYE_COLORS[e] ? e : 'neutral'; S.emotionUntil = now() + hold; touch(); }
function setActivity(a) { S.activity = a; }
function touch() { S.lastInteraction = now(); }
function pointer(x, y) { S.pointer = [Math.max(-1, Math.min(1, x)), Math.max(-1, Math.min(1, y))]; }

const raycaster = new THREE.Raycaster();
function hitTest(ev) {
  const ndc = new THREE.Vector2((ev.clientX / innerWidth) * 2 - 1, -(ev.clientY / innerHeight) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObjects([nova.root, pip.root], true)[0];
  return hit ? hit.object.userData.who : null;
}

let press = null, clickTimer = null;
canvas.addEventListener('mousedown', ev => { if (ev.button === 0) press = { x: ev.screenX, y: ev.screenY, lx: ev.screenX, ly: ev.screenY, moved: false }; });
window.addEventListener('mousemove', ev => {
  if (!bridge()) pointer((ev.clientX / innerWidth - 0.6) * 2, (ev.clientY / innerHeight - 0.45) * 2);
  if (!press) return;
  const dx = ev.screenX - press.lx, dy = ev.screenY - press.ly;
  if (Math.abs(ev.screenX - press.x) + Math.abs(ev.screenY - press.y) > 5) press.moved = true;
  if (press.moved && (dx || dy)) { bridge()?.move_by(dx, dy); press.lx = ev.screenX; press.ly = ev.screenY; }
});
window.addEventListener('mouseup', ev => {
  if (!press || ev.button !== 0) return;
  const moved = press.moved; press = null;
  if (moved || menu.contains(ev.target)) return;
  const who = hitTest(ev);
  if (who === 'pip') return petPip();
  if (who === 'nova') {
    clearTimeout(clickTimer);
    const x = ev.clientX, y = ev.clientY;
    clickTimer = setTimeout(() => { touch(); openMenu(x, y); }, 230);
  } else closeMenu();
});
canvas.addEventListener('dblclick', ev => { clearTimeout(clickTimer); if (hitTest(ev)) { closeMenu(); openInput(); } });
window.addEventListener('contextmenu', ev => { ev.preventDefault(); openMenu(ev.clientX, ev.clientY); });
window.addEventListener('blur', closeMenu); // clicked somewhere else on the desktop

function openMenu(x, y) {
  menu.classList.add('show');
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  menu.style.left = Math.max(4, Math.min(x - mw / 2, innerWidth - mw - 4)) + 'px';
  menu.style.top = Math.max(4, Math.min(y - mh / 2, innerHeight - mh - 4)) + 'px';
}
function closeMenu() { menu.classList.remove('show'); }

menu.addEventListener('click', ev => {
  const act = ev.target.closest('[data-act]')?.dataset.act;
  if (!act) return;
  closeMenu();
  const api = bridge();
  if (act === 'talk') api?.clicked();
  else if (act === 'type') openInput();
  else if (act === 'pet') petPip();
  else if (act === 'voice') api?.next_voice();
  else if (act === 'mute') api?.toggle_mute();
  else if (act === 'quiet') api?.quiet(60);
  else if (act === 'hide') api?.hide();
  else if (act === 'quit') api?.quit();
  else perform(act);
});

function openInput() { touch(); input.classList.add('show'); input.focus(); }
input.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') { input.value = ''; input.classList.remove('show'); }
  if (ev.key === 'Enter') { const text = input.value.trim(); input.value = ''; input.classList.remove('show'); if (text) bridge()?.text(text); }
});

function petPip() {
  S.petHappyUntil = now() + 2.5;
  for (let i = 0; i < 5; i++) spawn('heart', pip.root.position.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.5, 0.9, 0.2)), { size: 0.13 + Math.random() * 0.08, vel: [(Math.random() - 0.5) * 0.3, 0.5, 0] });
  touch();
  bridge()?.pet();
}

// public API used by Python (evaluate_js) and by tests
window.nova = { setEmotion, setActivity, say, perform, pointer, wake: touch, enableShaped, openMenu, state: S, _debug: { nova, pip, cur: () => cur } };
if (params.get('bg')) document.body.style.background = params.get('bg');
if (params.has('shaped')) enableShaped();
requestAnimationFrame(frame);
window.addEventListener('pywebviewready', () => bridge()?.ready());
if (window.pywebview) bridge()?.ready();
