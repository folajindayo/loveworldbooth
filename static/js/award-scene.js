// The Award Room: floating, procedurally built award objects behind the public pages.
// Everything is generated in code (no model downloads), so the whole scene is three.js + one texture.
// The page works without it: if WebGL or the CDN fails, the canvas simply stays empty.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const canvas = document.getElementById("scene");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const lowEnd = (navigator.hardwareConcurrency || 4) <= 4 || (navigator.deviceMemory || 4) <= 3;

const renderer = new THREE.WebGLRenderer({ canvas, antialias: !lowEnd, alpha: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, lowEnd ? 1.25 : 1.75));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
camera.position.set(0, 0, 14);

const key = new THREE.DirectionalLight(0xffe2a8, 2.2);
key.position.set(4, 6, 6);
const rim = new THREE.DirectionalLight(0x7d8cff, 1.4);
rim.position.set(-6, -2, -4);
scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.15));

// ---- materials ----------------------------------------------------------------------------------
const gold = new THREE.MeshStandardMaterial({ color: 0xd9a641, metalness: 1, roughness: 0.22 });
const goldBright = new THREE.MeshStandardMaterial({ color: 0xf2c76b, metalness: 1, roughness: 0.15 });
const crystal = lowEnd
  ? new THREE.MeshStandardMaterial({ color: 0xd6e4ff, metalness: 0.85, roughness: 0.04, transparent: true, opacity: 0.5 })
  : new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0, roughness: 0.03, transmission: 1, thickness: 0.6,
      ior: 1.5, clearcoat: 1, attenuationColor: new THREE.Color(0xa9c4ff), attenuationDistance: 2.5 });

// ---- objects ------------------------------------------------------------------------------------
const floaters = [];
function float(obj, { x, y, z, scale = 1, spin = 0.2, bob = 0.25, depth = 1 }) {
  obj.position.set(x, y, z);
  obj.scale.setScalar(scale);
  obj.userData = { base: new THREE.Vector3(x, y, z), spin, bob, depth, phase: Math.random() * Math.PI * 2, kick: 0 };
  scene.add(obj);
  floaters.push(obj);
  return obj;
}

// 1. Logo medallion: gold coin with the logo enamelled on both faces (the hero object; tap it).
const medallion = new THREE.Group();
const coin = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 0.16, 96, 1, true), gold);
coin.rotation.x = Math.PI / 2;
const faceTex = new THREE.TextureLoader().load("/brand/medallion.jpg", (t) => { t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; });
const faceMat = new THREE.MeshStandardMaterial({ map: faceTex, metalness: 0.55, roughness: 0.32 });
const front = new THREE.Mesh(new THREE.CircleGeometry(1, 96), faceMat);
front.position.z = 0.081;
const back = front.clone();
back.position.z = -0.081;
back.rotation.y = Math.PI;
const lip = new THREE.Mesh(new THREE.TorusGeometry(1, 0.05, 16, 120), goldBright);
const lipBack = lip.clone();
lip.position.z = 0.08;
lipBack.position.z = -0.08;
medallion.add(coin, front, back, lip, lipBack);

// 2. Crystal plaque: the Certificate of Presence silhouette in glass on a gold base.
function plaqueShape() {
  const s = new THREE.Shape();
  s.moveTo(-0.7, -1);
  s.lineTo(0.7, -1);
  s.lineTo(0.85, 0.35);
  s.lineTo(0.35, 1.15);
  s.quadraticCurveTo(-0.5, 1.05, -0.85, 0.45);
  s.lineTo(-0.7, -1);
  return s;
}
const plaque = new THREE.Group();
const glass = new THREE.Mesh(
  new THREE.ExtrudeGeometry(plaqueShape(), { depth: 0.22, bevelEnabled: true, bevelSize: 0.05, bevelThickness: 0.05, bevelSegments: 3 }),
  crystal,
);
glass.position.z = -0.11;
const base = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.28, 0.6), gold);
base.position.y = -1.18;
const plate = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.16, 0.02), goldBright);
plate.position.set(0, 0.05, 0.17);
plaque.add(glass, base, plate);

// 3. Gold wire globe.
const globe = new THREE.Group();
for (let i = 0; i < 6; i++) {
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1, 0.018, 8, 96), gold);
  ring.rotation.y = (i / 6) * Math.PI;
  globe.add(ring);
}
for (const lat of [-0.6, -0.3, 0, 0.3, 0.6]) {
  const r = Math.cos(Math.asin(lat));
  const ring = new THREE.Mesh(new THREE.TorusGeometry(r, 0.016, 8, 96), gold);
  ring.rotation.x = Math.PI / 2;
  ring.position.y = lat;
  globe.add(ring);
}
globe.add(new THREE.Mesh(new THREE.SphereGeometry(0.93, 32, 24), new THREE.MeshStandardMaterial({ color: 0x0c1230, metalness: 0.4, roughness: 0.5 })));

// 4. Gold stars.
function star() {
  const s = new THREE.Shape();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 ? 0.42 : 1;
    const a = (i / 10) * Math.PI * 2 + Math.PI / 2;
    i ? s.lineTo(Math.cos(a) * r, Math.sin(a) * r) : s.moveTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  return new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: 0.18, bevelEnabled: true, bevelSize: 0.06, bevelThickness: 0.06, bevelSegments: 2 }), goldBright);
}

// Layout: objects sit around the edges so the name and video stay clear. Positions differ by aspect.
// [x, y, z, scale] per object. The result page keeps the video column clear; the landing page frames the headline.
const LAYOUTS = {
  landing: {
    portrait: { medallion: [1.55, 3.6, 0, 1.05], plaque: [-1.9, -3.4, -1, 0.9], globe: [-1.8, 4.6, -3, 0.75], s1: [2.1, -2.2, -2, 0.32], s2: [-2.3, 1.2, -4, 0.25], s3: [1.9, 0.6, -6, 0.22] },
    landscape: { medallion: [4.6, 1.4, 0, 1.35], plaque: [-4.9, -1.2, -1, 1.15], globe: [-3.8, 2.6, -3, 0.9], s1: [3.6, -2.4, -2, 0.36], s2: [-1.4, 3.1, -4, 0.26], s3: [5.6, -0.4, -6, 0.24] },
  },
  result: {
    portrait: { medallion: [1.85, 3.95, 1, 0.55], plaque: [-2.0, -4.2, -2, 0.8], globe: [-1.9, 4.5, -3, 0.6], s1: [2.2, -3.2, -2, 0.3], s2: [-2.4, 0.6, -5, 0.22], s3: [2.3, 0.2, -6, 0.2] },
    landscape: { medallion: [0.4, 2.9, -1, 0.95], plaque: [-5.2, -3.0, -2, 1.0], globe: [6.4, 3.0, -3, 0.8], s1: [0.6, -2.9, -2, 0.32], s2: [-1.6, 3.6, -5, 0.24], s3: [6.2, -2.6, -6, 0.24] },
  },
};
function layout() {
  const page = LAYOUTS[document.body.dataset.scene] || LAYOUTS.landing;
  return innerHeight > innerWidth ? page.portrait : page.landscape;
}
const objects = {
  medallion: float(medallion, { x: 0, y: 0, z: 0, spin: 0.35, bob: 0.22, depth: 1.2 }),
  plaque: float(plaque, { x: 0, y: 0, z: 0, spin: -0.18, bob: 0.18, depth: 1 }),
  globe: float(globe, { x: 0, y: 0, z: 0, spin: 0.25, bob: 0.15, depth: 0.7 }),
  s1: float(star(), { x: 0, y: 0, z: 0, spin: 0.6, bob: 0.3, depth: 0.9 }),
  s2: float(star(), { x: 0, y: 0, z: 0, spin: -0.5, bob: 0.25, depth: 0.6 }),
  s3: float(star(), { x: 0, y: 0, z: 0, spin: 0.4, bob: 0.2, depth: 0.5 }),
};
function applyLayout() {
  const L = layout();
  for (const [name, [x, y, z, s]] of Object.entries(L)) {
    const o = objects[name];
    o.userData.base.set(x, y, z);
    o.scale.setScalar(s);
  }
}

// 5. Sparkles drifting upward.
const N = lowEnd ? 90 : 220;
const sparkGeo = new THREE.BufferGeometry();
const sparkPos = new Float32Array(N * 3);
for (let i = 0; i < N; i++) {
  sparkPos[i * 3] = (Math.random() - 0.5) * 16;
  sparkPos[i * 3 + 1] = (Math.random() - 0.5) * 14;
  sparkPos[i * 3 + 2] = -Math.random() * 8;
}
sparkGeo.setAttribute("position", new THREE.BufferAttribute(sparkPos, 3));
const sparks = new THREE.Points(sparkGeo, new THREE.PointsMaterial({ color: 0xf6d28a, size: 0.045, transparent: true, opacity: 0.75, depthWrite: false }));
scene.add(sparks);

// Confetti burst (tap the medallion, or call window.awardScene.celebrate()).
const confetti = [];
const confettiGeo = new THREE.PlaneGeometry(0.09, 0.16);
const confettiMats = [0xf2c76b, 0xd9a641, 0xfff4d6, 0x7d8cff].map(
  (c) => new THREE.MeshStandardMaterial({ color: c, metalness: 0.8, roughness: 0.3, side: THREE.DoubleSide }));
function celebrate(origin = objects.medallion.position) {
  for (let i = 0; i < (lowEnd ? 60 : 140); i++) {
    const m = new THREE.Mesh(confettiGeo, confettiMats[i % confettiMats.length]);
    m.position.copy(origin);
    const a = Math.random() * Math.PI * 2;
    const v = 3 + Math.random() * 5;
    m.userData = { vel: new THREE.Vector3(Math.cos(a) * v, Math.sin(a) * v + 4, (Math.random() - 0.5) * 4),
                   rot: new THREE.Vector3(Math.random() * 8, Math.random() * 8, Math.random() * 8), life: 2.6 };
    scene.add(m);
    confetti.push(m);
  }
  objects.medallion.userData.kick = 14;
  if (reduceMotion) requestAnimationFrame(tick);  // the loop is idle when motion is reduced
}

// ---- input: pointer / tilt parallax, scroll depth, tap on medallion ------------------------------
const target = { x: 0, y: 0 };
addEventListener("pointermove", (e) => {
  target.x = (e.clientX / innerWidth - 0.5) * 2;
  target.y = (e.clientY / innerHeight - 0.5) * 2;
}, { passive: true });
addEventListener("deviceorientation", (e) => {
  if (e.gamma == null) return;
  target.x = Math.max(-1, Math.min(1, e.gamma / 30));
  target.y = Math.max(-1, Math.min(1, (e.beta - 45) / 30));
}, { passive: true });

const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();
addEventListener("pointerdown", (e) => {
  if (e.target.closest("a, button, video, input")) return;
  ndc.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  if (ray.intersectObject(objects.medallion, true).length) celebrate();
});

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  applyLayout();
}
addEventListener("resize", resize);
resize();

// ---- loop ---------------------------------------------------------------------------------------
const clock = new THREE.Clock();
let entrance = reduceMotion ? 1 : 0;
let running = true;
document.addEventListener("visibilitychange", () => { running = !document.hidden; if (running) { clock.getDelta(); tick(); } });

function tick() {
  if (!running) return;
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.elapsedTime;
  entrance = Math.min(1, entrance + dt / 1.8);
  const ease = 1 - Math.pow(1 - entrance, 3);
  const scroll = scrollY / Math.max(innerHeight, 1);

  camera.position.x += (target.x * 0.6 - camera.position.x) * 0.04;
  camera.position.y += (-target.y * 0.4 - camera.position.y) * 0.04;
  camera.lookAt(0, 0, 0);

  for (const o of floaters) {
    const d = o.userData;
    const motion = reduceMotion ? 0 : 1;
    o.position.x = d.base.x * (0.6 + 0.4 * ease);
    o.position.y = d.base.y + Math.sin(t * 0.9 + d.phase) * d.bob * motion + scroll * 2.2 * d.depth - (1 - ease) * 3;
    o.position.z = d.base.z;
    d.kick *= 0.95;
    o.rotation.y += (d.spin + d.kick) * dt * motion;
    o.rotation.x = Math.sin(t * 0.5 + d.phase) * 0.15 * motion;
  }

  const p = sparkGeo.attributes.position;
  for (let i = 0; i < N; i++) {
    p.array[i * 3 + 1] += dt * 0.25;
    if (p.array[i * 3 + 1] > 7) p.array[i * 3 + 1] = -7;
  }
  p.needsUpdate = true;

  for (let i = confetti.length - 1; i >= 0; i--) {
    const m = confetti[i], u = m.userData;
    u.vel.y -= 9.8 * dt;
    u.vel.multiplyScalar(0.985);
    m.position.addScaledVector(u.vel, dt);
    m.rotation.x += u.rot.x * dt; m.rotation.y += u.rot.y * dt; m.rotation.z += u.rot.z * dt;
    u.life -= dt;
    if (u.life <= 0) { scene.remove(m); confetti.splice(i, 1); }
  }

  renderer.render(scene, camera);
  if (!reduceMotion || confetti.length) requestAnimationFrame(tick);
}
tick();
document.documentElement.classList.add("scene-ready");
window.awardScene = { celebrate };
