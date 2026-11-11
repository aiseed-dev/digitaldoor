// デジタルドアの立体図(/3d/)の動き。three.js(/js/three.min.js、r128)の後に読む。
// 外へは何も送らない。模様は canvas に描く。
(() => {
'use strict';
const $ = s => document.querySelector(s);
const stage = $('#stage'), canvas = $('#c'), labelsEl = $('#labels');
if (typeof THREE === 'undefined') { $('#nogl').hidden = false; return; }
let renderer;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true }); }
catch (e) { $('#nogl').hidden = false; return; }

const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const lerp = (a, b, k) => a + (b - a) * k;
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const sstep = (a, b, v) => { const k = clamp((v - a) / (b - a)); return k * k * (3 - 2 * k); };
const ease = k => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
const lin = k => k;
const rad = d => d * Math.PI / 180;
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const col = h => new THREE.Color(h).convertSRGBToLinear();
const noop = () => {};

renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = .9;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.setClearColor(0x000000, 0);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 200);
const world = new THREE.Group();
scene.add(world);

/* ---------- 手で描く模様と、映り込みの空 ---------- */
function tex(w, h, draw, rx = 1, ry = 1) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(rx, ry);
  t.encoding = THREE.sRGBEncoding; t.anisotropy = 4;
  return t;
}
const speckle = (base, n, a) => (g, w, h) => {
  g.fillStyle = base; g.fillRect(0, 0, w, h);
  for (let i = 0; i < n; i++) {
    g.fillStyle = `rgba(${Math.random() < .5 ? '255,255,255' : '0,0,0'},${Math.random() * a})`;
    const s = 1 + Math.random() * 2;
    g.fillRect(Math.random() * w, Math.random() * h, s, s);
  }
};
function envTexture() {
  const face = (top, bot, sun) => {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d'), gr = g.createLinearGradient(0, 0, 0, 64);
    gr.addColorStop(0, top); gr.addColorStop(1, bot); g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
    if (sun) { const r = g.createRadialGradient(22, 16, 2, 22, 16, 26); r.addColorStop(0, 'rgba(255,250,235,1)'); r.addColorStop(1, 'rgba(255,250,235,0)'); g.fillStyle = r; g.fillRect(0, 0, 64, 64); }
    return c;
  };
  const side = () => face('#dfe9f3', '#8f9396');
  const cube = new THREE.CubeTexture([side(), side(), face('#eef4fa', '#dfe9f3'), face('#8a8c8c', '#77797a'), face('#e6eef6', '#9a9da0', true), face('#b9b3a8', '#8a857c')]);
  cube.encoding = THREE.sRGBEncoding; cube.needsUpdate = true;
  const pm = new THREE.PMREMGenerator(renderer);
  const out = pm.fromCubemap(cube).texture; pm.dispose();
  return out;
}
scene.environment = envTexture();

/* ---------- 材料 ---------- */
const solids = [];
function mat(o) {
  const m = new THREE.MeshStandardMaterial(o);
  m.userData.base = o.opacity === undefined ? 1 : o.opacity; m.userData.xr = 1;
  solids.push(m); return m;
}
const M = {
  concrete: mat({ color: col('#9b9c9a'), roughness: .95 }),
  floor: mat({ map: tex(128, 128, speckle('#8f9698', 900, .16), 7, 3), roughness: .8 }),
  wall: mat({ map: tex(128, 128, speckle('#d5cebf', 1400, .12), 1.7, 1.7), roughness: .95 }),
  frame: mat({ color: col('#3a3e42'), metalness: .5, roughness: .45 }),
  leaf: mat({ color: col('#27384f'), metalness: .35, roughness: .48 }),
  metal: mat({ color: col('#c9cdd1'), metalness: 1, roughness: .28 }),
  dark: mat({ color: col('#1c1f22'), metalness: .3, roughness: .5 }),
  groove: mat({ color: col('#0e1114'), roughness: .7 }),
  glass: mat({ color: col('#1a2a33'), metalness: .6, roughness: .1 }),
  phoneBody: mat({ color: col('#eceef0'), roughness: .5 }),
  mb: mat({ color: col('#a8adb1'), metalness: .45, roughness: .5 }),
  alu: mat({ color: col('#b9bec3'), metalness: .9, roughness: .35 }),
  tile: mat({ map: tex(128, 128, (g, w, h) => { speckle('#b8b2a6', 500, .1)(g, w, h); g.strokeStyle = '#8f897e'; g.lineWidth = 3; g.strokeRect(0, 0, w, h); }, 6, 4), roughness: .6 }),
  wood: mat({ map: tex(256, 64, (g, w, h) => { const c = ['#b98d5f', '#c39a6b', '#b08355', '#bd9163']; for (let i = 0; i < 4; i++) { g.fillStyle = c[i]; g.fillRect(i * w / 4, 0, w / 4, h); g.fillStyle = 'rgba(60,35,10,.35)'; g.fillRect(i * w / 4, 0, 1.5, h); } }, 3, 1), roughness: .5 }),
  room: mat({ color: col('#efeae0'), roughness: .95 }),
  ceil: mat({ color: col('#f4f1ea'), roughness: 1 }),
  cab: mat({ color: col('#d9cfbf'), roughness: .6 }),
  rug: mat({ color: col('#5b6a57'), roughness: 1 }),
  glow: mat({ color: col('#fff1d6'), emissive: col('#ffd9a0'), emissiveIntensity: 1.1, roughness: .9 }),
  lock: mat({ color: col('#8a9097'), metalness: .8, roughness: .35, emissive: col('#3d8fe0'), emissiveIntensity: 0 }),
  bolt: mat({ color: col('#d9dde0'), metalness: 1, roughness: .2 }),
  ctrl: mat({ color: col('#f1f2f3'), roughness: .5, emissive: col('#3d8fe0'), emissiveIntensity: 0 }),
  reader: mat({ color: col('#eceef0'), roughness: .5, emissive: col('#3d8fe0'), emissiveIntensity: 0 }),
};
M.leaf.userData.xr = .16; M.wall.userData.xr = .34; M.frame.userData.xr = .5;
M.wall.bumpMap = M.wall.map; M.wall.bumpScale = .004;

const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true });
const wireMat = new THREE.LineBasicMaterial({ color: col('#3d8fe0'), transparent: true, depthTest: false });
const dashMat = new THREE.LineDashedMaterial({ color: col('#3d8fe0'), transparent: true, depthTest: false, dashSize: .06, gapSize: .05 });

function edge(o) { const l = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 25), lineMat); o.add(l); return l; }
function box(w, h, d, m, x, y, z, parent, opt) {
  const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  o.position.set(x, y, z); o.castShadow = !(opt && opt.cast === false); o.receiveShadow = true;
  (parent || world).add(o); if (opt && opt.edge) edge(o); return o;
}
function cyl(r, h, m, x, y, z, parent, opt) {
  const o = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 24), m);
  o.rotation.x = Math.PI / 2; o.position.set(x, y, z); o.castShadow = true; o.receiveShadow = true;
  (parent || world).add(o); if (opt && opt.edge) edge(o); return o;
}

/* ---------- 建物: 廊下、壁、玄関の中 ---------- */
box(4.4, .15, 5.0, M.concrete, 0, -.075, -.8);
box(4.4, .006, 1.7, M.floor, 0, .003, .85, null, { cast: false });
box(4.4, .15, 1.9, M.concrete, 0, 2.575, .77); // 上の階の廊下の床
{ // 開口のある壁
  const s = new THREE.Shape(); s.moveTo(-2.2, 0); s.lineTo(2.2, 0); s.lineTo(2.2, 2.5); s.lineTo(-2.2, 2.5); s.lineTo(-2.2, 0);
  const hole = new THREE.Path(); hole.moveTo(-.425, 0); hole.lineTo(-.425, 2.0); hole.lineTo(.425, 2.0); hole.lineTo(.425, 0); hole.lineTo(-.425, 0);
  s.holes.push(hole);
  const o = new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: .18, bevelEnabled: false }), M.wall);
  o.position.z = -.18; o.castShadow = o.receiveShadow = true; world.add(o); edge(o);
}
box(.045, 2.045, .202, M.frame, -.4475, 1.0225, -.089, null, { edge: true });
box(.045, 2.045, .202, M.frame, .4475, 1.0225, -.089, null, { edge: true });
box(.94, .045, .202, M.frame, 0, 2.0225, -.089, null, { edge: true });
box(.85, .012, .2, M.metal, 0, .006, -.09);
box(.85, .03, .03, M.frame, 0, 1.985, -.08); box(.03, 2.0, .03, M.frame, -.41, 1.0, -.08); box(.03, 2.0, .03, M.frame, .41, 1.0, -.08); // 戸当り
for (const y of [.25, 1.0, 1.75]) { const h = cyl(.008, .1, M.metal, .405, y, -.012); h.rotation.x = 0; }

// メーターボックスの扉
box(.62, 1.88, .02, M.mb, -1.45, .96, .004, null, { edge: true });
box(.03, .09, .012, M.dark, -1.2, .95, .02);
for (let i = 0; i < 5; i++) box(.4, .012, .006, M.dark, -1.45, 1.6 + i * .035, .016, null, { cast: false });
// 面格子のある窓
box(.86, .96, .03, M.alu, 1.35, 1.45, 0, null, { edge: true });
box(.78, .88, .012, M.glass, 1.35, 1.45, .012);
for (let i = 0; i < 8; i++) box(.012, 1.0, .012, M.alu, 1.0 + i * .1, 1.45, .055);
box(.86, .016, .016, M.alu, 1.35, 1.78, .055); box(.86, .016, .016, M.alu, 1.35, 1.12, .055);

// ドアホン(読取機を兼ねる)と表札
const readerBody = box(.11, .17, .028, M.reader, -.66, 1.38, .014, null, { edge: true });
cyl(.012, .006, M.dark, -.66, 1.435, .03);
cyl(.011, .006, M.metal, -.66, 1.318, .03);
for (let i = 0; i < 3; i++) box(.05, .003, .002, M.dark, -.66, 1.405 - i * .008, .029, null, { cast: false });
const ringMat = new THREE.MeshBasicMaterial({ color: col('#8d98a5') });
const ring = new THREE.Mesh(new THREE.TorusGeometry(.019, .0035, 10, 40), ringMat);
ring.position.set(-.66, 1.365, .03); world.add(ring);
const readerPos = V(-.66, 1.365, .03);
const plate = mat({ map: tex(128, 52, (g, w, h) => { g.fillStyle = '#e9e7e1'; g.fillRect(0, 0, w, h); g.fillStyle = '#23282d'; g.font = '600 30px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('301', w / 2, h / 2 + 2); }), roughness: .5 });
box(.11, .045, .006, plate, -.66, 1.53, .003, null, { edge: true });

// 玄関の中
box(1.7, .01, 1.1, M.tile, 0, .005, -.73, null, { cast: false });
box(1.7, .10, 1.9, M.wood, 0, .05, -2.23);
box(.12, 2.4, 3.0, M.room, -.91, 1.2, -1.68); box(.12, 2.4, 3.0, M.room, .91, 1.2, -1.68);
box(1.94, 2.4, .12, M.room, 0, 1.2, -3.24);
box(1.94, .12, 3.12, M.ceil, 0, 2.46, -1.74);
box(.34, .95, .9, M.cab, .68, .485, -.75);
box(.5, .008, .35, M.rug, -.1, .014, -.55, null, { cast: false });
box(.72, 1.9, .02, M.glow, 0, 1.05, -3.17, null, { cast: false });
box(.8, 1.98, .012, M.cab, 0, 1.05, -3.176, null, { cast: false });
{ const lamp = new THREE.Mesh(new THREE.CylinderGeometry(.13, .13, .025, 28), M.glow); lamp.position.set(0, 2.385, -1.2); world.add(lamp); }

// 扉のコントローラ(室内側、扉の上)
const ctrlBox = box(.17, .10, .045, M.ctrl, 0, 2.17, -.204);
const ledMat = new THREE.MeshBasicMaterial({ color: col('#8d98a5') });
{ const led = new THREE.Mesh(new THREE.SphereGeometry(.007, 12, 12), ledMat); led.position.set(.06, 2.17, -.23); world.add(led); }
const ctrlPos = V(0, 2.17, -.204);

/* ---------- 扉 ---------- */
const hinge = new THREE.Group(); hinge.position.set(.40, 0, -.045); world.add(hinge);
box(.80, 1.972, .04, M.leaf, -.40, .998, 0, hinge, { edge: true });
box(.05, .40, .004, M.metal, -.71, 1.0, .022, hinge);
box(.026, .30, .02, M.metal, -.71, 1.0, .064, hinge, { edge: true });
box(.02, .02, .042, M.metal, -.71, 1.11, .043, hinge); box(.02, .02, .042, M.metal, -.71, .89, .043, hinge);
const keyCyl = cyl(.018, .012, M.metal, -.71, 1.27, .026, hinge, { edge: true });
box(.30, .06, .006, M.metal, -.40, .90, .023, hinge, { edge: true });
box(.26, .03, .004, M.dark, -.40, .90, .027, hinge, { cast: false });
cyl(.012, .01, M.metal, -.40, 1.48, .022, hinge, { edge: true });
// 室内側
box(.026, .30, .02, M.metal, -.71, 1.0, -.064, hinge);
box(.02, .02, .042, M.metal, -.71, 1.11, -.043, hinge); box(.02, .02, .042, M.metal, -.71, .89, -.043, hinge);
const thumb = box(.03, .055, .02, M.metal, -.71, 1.27, -.031, hinge);
box(.20, .05, .05, M.dark, -.20, 1.90, -.046, hinge);
// 電子錠(扉の中)
const lockCase = box(.085, .17, .028, M.lock, -.745, 1.05, 0, hinge, { cast: false });
const bolt = box(.05, .028, .016, M.bolt, -.81, 1.05, 0, hinge, { cast: false });
// 意匠
const designs = { flat: new THREE.Group(), yoko: new THREE.Group(), tate: new THREE.Group() };
for (const y of [.42, .66, 1.24, 1.66]) box(.74, .006, .003, M.groove, -.40, y, .0205, designs.yoko, { cast: false, edge: true });
box(.07, 1.15, .004, M.glass, -.20, 1.18, .0205, designs.tate, { cast: false, edge: true });
box(.006, 1.5, .003, M.groove, -.30, 1.0, .0205, designs.tate, { cast: false });
for (const k in designs) { designs[k].visible = k === 'flat'; hinge.add(designs[k]); }

/* ---------- 線: 配線、発行と連携のつながり、寸法 ---------- */
function line(points, m, parent) {
  const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), m);
  if (m.isLineDashedMaterial) l.computeLineDistances();
  l.renderOrder = 5; (parent || world).add(l); return l;
}
const issuePos = V(1.25, 2.28, .45), bizPos = V(1.3, .62, .5), tapPos = V(-.655, 1.33, .085);
const pathReaderCtrl = [readerPos.clone(), V(-.66, 1.365, -.2), V(-.66, 2.17, -.2), ctrlPos.clone()];
const pathCtrlHinge = [ctrlPos.clone(), V(.44, 2.17, -.204), V(.44, 1.05, -.204), V(.40, 1.05, -.045)];
line(pathReaderCtrl, wireMat); line(pathCtrlHinge, wireMat);
line([V(0, 1.05, 0), V(-.745, 1.05, 0)], wireMat, hinge);
const linkIssue = line([issuePos, tapPos], dashMat), linkBiz = line([ctrlPos, bizPos], dashMat);

const dims = new THREE.Group(); world.add(dims);
{ const p = [], seg = (a, b, c, d) => p.push(V(a, b, .02), V(c, d, .02));
  seg(-.425, 2.28, .425, 2.28); seg(-.425, 2.08, -.425, 2.33); seg(.425, 2.08, .425, 2.33);
  seg(-.445, 2.26, -.405, 2.30); seg(.405, 2.26, .445, 2.30);
  seg(.72, 0, .72, 2.0); seg(.50, 0, .77, 0); seg(.50, 2.0, .77, 2.0);
  seg(.70, -.02, .74, .02); seg(.70, 1.98, .74, 2.02);
  dims.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(p), lineMat)); }

function icon(draw) {
  const t = tex(128, 128, (g, w, h) => {
    g.fillStyle = '#1c5fa6'; g.beginPath(); const r = 26; g.moveTo(r, 0); g.arcTo(w, 0, w, h, r); g.arcTo(w, h, 0, h, r); g.arcTo(0, h, 0, 0, r); g.arcTo(0, 0, w, 0, r); g.fill();
    g.strokeStyle = g.fillStyle = '#fff'; g.lineWidth = 9; g.lineCap = 'round'; draw(g);
  });
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthTest: false }));
  s.scale.set(.24, .24, 1); s.renderOrder = 6; world.add(s); return s;
}
const issueNode = icon(g => { g.beginPath(); g.arc(46, 64, 17, 0, 7); g.stroke(); g.beginPath(); g.moveTo(63, 64); g.lineTo(100, 64); g.moveTo(88, 64); g.lineTo(88, 80); g.moveTo(100, 64); g.lineTo(100, 76); g.stroke(); });
const bizNode = icon(g => { for (const y of [42, 64, 86]) { g.beginPath(); g.moveTo(34, y); g.lineTo(94, y); g.stroke(); } });
issueNode.position.copy(issuePos); bizNode.position.copy(bizPos);

const pulseMat = new THREE.MeshBasicMaterial({ color: col('#3d8fe0'), depthTest: false, transparent: true });
const pulseDot = new THREE.Mesh(new THREE.SphereGeometry(.024, 16, 16), pulseMat);
pulseDot.renderOrder = 7; pulseDot.visible = false; scene.add(pulseDot);

// スマートフォン
const phone = new THREE.Group(); phone.visible = false; scene.add(phone);
{ const b = new THREE.Mesh(new THREE.BoxGeometry(.072, .15, .008), new THREE.MeshStandardMaterial({ color: col('#15181b'), roughness: .3, metalness: .4 })); b.castShadow = true; phone.add(b);
  const s = new THREE.Mesh(new THREE.PlaneGeometry(.064, .14), new THREE.MeshBasicMaterial({ color: col('#9cc8f5') })); s.position.z = .0045; phone.add(s); }

/* ---------- 明かり ---------- */
const hemi = new THREE.HemisphereLight(0xe8f0ff, 0x8c8478, .4); scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffe9c8, 1.3);
sun.position.set(-3.4, 2.9, 4.8); sun.target.position.set(0, 1, -.4); scene.add(sun, sun.target);
sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -3.6, right: 3.6, top: 3.4, bottom: -2.4, near: .5, far: 14 });
sun.shadow.bias = -.0004; sun.shadow.normalBias = .02;
const lamp = new THREE.PointLight(0xffd9a8, .6, 7, 2); lamp.position.set(0, 2.2, -1.2); scene.add(lamp);

/* ---------- 動かす仕組み ---------- */
const tweens = new Set(), runs = {}, CANCEL = Symbol('cancel');
const bump = g => { runs[g] = (runs[g] || 0) + 1; };
function tween(group, ms, step, ez, real) {
  if (reduce && !real) ms = 1;
  return new Promise((res, rej) => tweens.add({ group, id: runs[group] || 0, t0: performance.now(), ms: Math.max(1, ms), step, ez: ez || ease, res, rej }));
}
const wait = ms => tween('scene', ms, noop, lin, true);
function stepTweens(now) {
  for (const a of [...tweens]) {
    if (a.id !== (runs[a.group] || 0)) { tweens.delete(a); a.rej(CANCEL); continue; }
    const k = clamp((now - a.t0) / a.ms); a.step(a.ez(k));
    if (k >= 1) { tweens.delete(a); a.res(); }
  }
}
const go = p => p.catch(noop);

/* ---------- 状態 ---------- */
const P = {
  front: { target: V(0, 1.15, 0), az: -.44, pol: 1.37, r: 4.3, fov: 38 },
  xray: { target: V(0, 1.3, -.1), az: -.78, pol: 1.2, r: 4.5, fov: 38 },
  inside: { target: V(0, 1.12, -.3), az: Math.PI + .1, pol: 1.5, r: 2.6, fov: 58 },
};
const K = P.front.r * Math.tan(rad(P.front.fov / 2));
const o = { target: P.front.target.clone(), az: P.front.az, pol: P.front.pol, r: P.front.r, fov: P.front.fov };
const cam = { free: false, locked: false };
let t = reduce ? 1 : 0, xray = 0, xrayOn = false, inside = false, insideK = 0, linkOp = 0, netOn = true, fit = 1, W = 1, H = 1;
let doorAngle = 0, boltOut = 1;

function applyDoor() { hinge.rotation.y = rad(doorAngle); bolt.position.x = lerp(-.775, -.81, boltOut); thumb.rotation.z = lerp(Math.PI / 2, 0, boltOut); }
const doorTo = (deg, ms) => { const a = doorAngle; return tween('scene', ms, k => { doorAngle = lerp(a, deg, k); applyDoor(); }); };
const boltTo = (out, ms = 260) => { const a = boltOut, b = out ? 1 : 0; return tween('scene', ms, k => { boltOut = lerp(a, b, k); applyDoor(); }); };

function camTo(p, ms) {
  bump('cam');
  const s = { az: o.az, pol: o.pol, r: o.r, fov: o.fov, target: o.target.clone() };
  return tween('cam', ms, k => { o.az = lerp(s.az, p.az, k); o.pol = lerp(s.pol, p.pol, k); o.r = lerp(s.r, p.r, k); o.fov = lerp(s.fov, p.fov, k); o.target.lerpVectors(s.target, p.target, k); });
}
function orbitPos(p, out) {
  const rr = p.r * (inside ? 1 : fit), sp = Math.sin(p.pol);
  return (out || V(0, 0, 0)).set(p.target.x + rr * sp * Math.sin(p.az), p.target.y + rr * Math.cos(p.pol), p.target.z + rr * sp * Math.cos(p.az));
}
function placeCamera() {
  if (t < 1) { // 図面と立体のあいだは、つまみが視点を決める
    const e = ease(sstep(.05, .95, t));
    o.fov = lerp(5, P.front.fov, e); o.r = K / Math.tan(rad(o.fov / 2));
    o.az = lerp(0, P.front.az, e); o.pol = lerp(Math.PI / 2, P.front.pol, e);
    o.target.set(0, lerp(1.25, P.front.target.y, e), 0);
  }
  if (!cam.free) { orbitPos(o, camera.position); camera.lookAt(o.target); }
  if (Math.abs(camera.fov - o.fov) > .01) { camera.fov = o.fov; camera.updateProjectionMatrix(); }
}
function applyMorph() {
  world.scale.z = lerp(.001, 1, sstep(.05, .8, t));
  const f = sstep(.1, .55, t), le = 1 - sstep(.45, .95, t);
  for (const m of solids) {
    const op = m.userData.base * f * lerp(1, m.userData.xr, xray);
    m.opacity = op; m.transparent = op < .995; m.depthWrite = op > .6; m.visible = op > .004;
  }
  lineMat.opacity = le; lineMat.visible = le > .004;
  const wo = xray * (t >= 1 ? 1 : 0); wireMat.opacity = wo; wireMat.visible = wo > .01;
  const lo = Math.max(xray, linkOp) * (netOn ? 1 : .18) * (t >= 1 ? 1 : 0);
  dashMat.opacity = lo; dashMat.visible = lo > .01;
  issueNode.material.opacity = bizNode.material.opacity = lo; issueNode.visible = bizNode.visible = lo > .01;
  $('#bp').style.opacity = 1 - sstep(.12, .7, t);
  $('#tblock').style.opacity = 1 - sstep(.15, .5, t);
  $('#hint').style.opacity = sstep(.9, 1, t);
  lamp.intensity = .6 * f; sun.intensity = 1.3 * f;
}

/* ---------- 札(立体の点に付く文字) ---------- */
const labs = [], tmp = V(0, 0, 0);
function lab(text, pos, cls, show) {
  const el = document.createElement('div'); el.className = 'lab ' + (cls || ''); el.innerHTML = '<i></i><span></span>'; el.lastChild.textContent = text;
  labelsEl.append(el); labs.push({ el, pos, show }); return el;
}
const bpShow = () => 1 - sstep(.2, .55, t), x3 = () => (t >= 1 ? xray : 0);
lab('W 850', () => tmp.set(0, 2.37, 0), 'dim', bpShow);
lab('H 2,000', () => tmp.set(.8, 1.0, 0), 'dim l', bpShow);
lab('ドアホン', () => tmp.set(-.66, 1.38, 0), 'note', bpShow);
lab('ドアポスト', () => tmp.set(-.3, .9, 0), 'note', bpShow);
lab('メーターボックス', () => tmp.set(-1.45, 1.2, 0), 'note', bpShow);
lab('面格子', () => tmp.set(1.35, 1.45, 0), 'note', bpShow);
const L = {
  reader: lab('ドアホン(読取機)', () => tmp.copy(readerPos), '', x3),
  lock: lab('電子錠', () => lockCase.getWorldPosition(tmp), '', () => Math.max(x3(), insideK)),
  ctrl: lab('扉のコントローラ', () => tmp.copy(ctrlPos), '', () => Math.max(x3(), insideK)),
  issue: lab('鍵の発行(管理者の手元)', () => tmp.copy(issuePos), 'b', () => (t >= 1 ? Math.max(xray, linkOp) : 0)),
  biz: lab('業務システムとの連携', () => tmp.copy(bizPos), 'b', () => (t >= 1 ? Math.max(xray, linkOp) : 0)),
};
lab('物理キー(非常用)', () => keyCyl.getWorldPosition(tmp), 'r', x3);
function placeLabels() {
  for (const l of labs) {
    const op = l.show();
    if (op < .03) { if (!l.el.hidden) l.el.hidden = true; continue; }
    const p = l.pos().project(camera);
    if (p.z > 1 || p.z < -1) { l.el.hidden = true; continue; }
    l.el.hidden = false; l.el.style.opacity = op;
    l.el.style.transform = `translate(${((p.x * .5 + .5) * W).toFixed(1)}px,${((-p.y * .5 + .5) * H).toFixed(1)}px)`;
  }
}

/* ---------- 画面の欄 ---------- */
const hud = $('#hud'), statusEl = $('#status'), logEl = $('#log');
const RING = { idle: '#8d98a5', run: '#3d8fe0', ok: '#27b376', ng: '#e2574c', warn: '#e0a030' };
const setRing = s => ringMat.color.copy(col(RING[s]));
const setLed = s => ledMat.color.copy(col(RING[s]));
function status(text, s, sub) {
  statusEl.hidden = !text; statusEl.dataset.s = s || '';
  statusEl.textContent = text || '';
  if (sub) { const e = document.createElement('small'); e.textContent = sub; statusEl.append(e); }
}
function hudReset(show, head) {
  hud.hidden = !show; $('#hudh').textContent = head || '扉の判断';
  hud.querySelectorAll('li').forEach(li => { li.dataset.s = ''; li.hidden = false; });
}
function setNet(on) {
  netOn = on; const n = $('#net'); n.dataset.s = on ? '' : 'off'; n.textContent = on ? '網 つながっています' : '網 切れています';
  L.issue.classList.toggle('off', !on); L.biz.classList.toggle('off', !on);
}
let seq = 140; const entries = [];
function log(kind, event, result, cls) {
  entries.unshift({ seq: ++seq, kind, event, result, cls: cls || '' });
  if (entries.length > 7) entries.pop();
  logEl.textContent = '';
  entries.forEach((e, i) => {
    const li = document.createElement('li'); li.className = e.cls + (i === 0 ? ' new' : '');
    for (const v of ['#' + String(e.seq).padStart(4, '0'), e.kind, e.event, e.result]) { const s = document.createElement('span'); s.textContent = v; li.append(s); }
    logEl.append(li);
  });
}
log('時刻', '同期', '同期済み'); log('認証', '成功', '署名つき資格情報'); log('操作', '解錠', '成功'); log('操作', '施錠', '自動施錠');
logEl.firstChild.classList.remove('new');

function pulse(points, ms, color) {
  const len = [0]; for (let i = 1; i < points.length; i++) len.push(len[i - 1] + points[i].distanceTo(points[i - 1]));
  const total = len[len.length - 1];
  pulseMat.color.copy(col(color || RING.run)); pulseDot.visible = true;
  return tween('scene', ms, k => {
    const d = k * total; let i = 1; while (i < len.length - 1 && len[i] < d) i++;
    pulseDot.position.lerpVectors(points[i - 1], points[i], (d - len[i - 1]) / Math.max(1e-6, len[i] - len[i - 1]));
  }, lin, true).then(() => { pulseDot.visible = false; });
}
function glow(m, to, ms = 300) { const a = m.emissiveIntensity; return tween('glow' + m.uuid, ms, k => { m.emissiveIntensity = lerp(a, to, k); }, ease, true); }
function reset() {
  doorAngle = 0; boltOut = 1; applyDoor(); phone.visible = false; pulseDot.visible = false;
  hudReset(false); status(''); $('#alarm').hidden = true; setNet(true); linkOp = 0; setRing('idle'); setLed('idle');
  for (const m of [M.lock, M.ctrl, M.reader]) { bump('glow' + m.uuid); m.emissiveIntensity = 0; }
  document.querySelectorAll('.rows .row.on').forEach(b => b.classList.remove('on'));
}
function play(btn, fn) {
  bump('scene'); reset(); if (btn) btn.classList.add('on');
  fn().catch(e => { if (e !== CANCEL) console.error(e); });
}
async function ensure3D() {
  if (t >= 1) return;
  bump('morph'); const a = t;
  await tween('morph', 1400 * (1 - a) + 200, k => setT(lerp(a, 1, k)));
}
function setT(v) {
  t = clamp(v); $('#morph').value = Math.round(t * 1000);
  if (t >= 1) { o.az = P.front.az; o.pol = P.front.pol; o.r = P.front.r; o.fov = P.front.fov; o.target.copy(P.front.target); }
}
function setXray(on, ms = 500) {
  xrayOn = on; $('#b-xray').setAttribute('aria-pressed', on);
  bump('xray'); const a = xray; return go(tween('xray', ms, k => { xray = lerp(a, on ? 1 : 0, k); }));
}
const fade = (name, get, set, to, ms = 400) => { bump(name); const a = get(); return go(tween(name, ms, k => set(lerp(a, to, k)))); };
const setLink = v => fade('link', () => linkOp, x => { linkOp = x; }, v);
const setInsideK = v => fade('ins', () => insideK, x => { insideK = x; }, v);

/* ---------- 中に入る、外に戻る ---------- */
const walkIn = V(0, 1.5, -1.5), walkEnd = V(.06, 1.5, -2.3);
function camFly(posPts, lookPts, ms) {
  const cp = new THREE.CatmullRomCurve3(posPts), cl = new THREE.CatmullRomCurve3(lookPts), a = V(0, 0, 0);
  return tween('scene', ms, k => { cp.getPoint(k, camera.position); cl.getPoint(k, a); camera.lookAt(a); });
}
function camTurn(from, to, th0, th1, ms) {
  const a = V(0, 0, 0);
  return tween('scene', ms, k => {
    camera.position.lerpVectors(from, to, k); const th = lerp(th0, th1, k);
    a.set(camera.position.x + Math.sin(th) * 2, camera.position.y - .22, camera.position.z - Math.cos(th) * 2); camera.lookAt(a);
  });
}
async function enter() {
  setXray(false); cam.locked = true;
  await camTo(P.front, 500);
  status('解錠', 'ok', '扉が確かめて、開けました'); setRing('ok'); setLed('ok');
  await boltTo(false); log('認証', '成功', '署名つき資格情報'); log('操作', '解錠', '成功');
  await doorTo(96, 1000);
  cam.free = true; const start = camera.position.clone();
  go(tween('fov', 2600, k => { o.fov = lerp(P.front.fov, P.inside.fov, k); }));
  await camFly([start, V(-.2, 1.5, 1.5), V(0, 1.5, .3), walkIn], [P.front.target.clone(), V(0, 1.4, -.8), V(0, 1.35, -2.4), V(0, 1.28, -3.5)], 3000);
  go(doorTo(0, 1300).then(() => boltTo(true)).then(() => { log('操作', '施錠', '自動施錠'); setRing('idle'); setLed('idle'); }));
  await camTurn(walkIn, walkEnd, 0, Math.PI, 2000);
  inside = true; setInsideK(1);
  const d = camera.position.clone().sub(P.inside.target); o.target.copy(P.inside.target); o.r = d.length();
  o.pol = Math.acos(clamp(d.y / o.r, -1, 1)); o.az = Math.atan2(d.x, d.z); if (o.az < 0) o.az += Math.PI * 2;
  cam.free = false; cam.locked = false;
  status('玄関の中', '', '扉の上の箱が、扉のコントローラです');
  $('#b-walk').textContent = '外に戻る';
  go(camTo(P.inside, 600));
}
async function leave() {
  cam.locked = true; setInsideK(0); cam.free = true;
  const from = camera.position.clone();
  await boltTo(false); await doorTo(96, 900);
  await camTurn(from, walkIn, Math.PI, 0, 1500);
  inside = false; Object.assign(o, { az: P.front.az, pol: P.front.pol, r: P.front.r }); o.target.copy(P.front.target);
  go(tween('fov', 2200, k => { o.fov = lerp(P.inside.fov, P.front.fov, k); }));
  await camFly([walkIn.clone(), V(0, 1.5, .3), V(-.2, 1.5, 1.5), orbitPos(P.front)], [V(0, 1.3, .5), V(-.1, 1.3, 2.5), V(0, 1.2, 1.0), P.front.target.clone()], 2600);
  cam.free = false; cam.locked = false; $('#b-walk').textContent = '中に入る';
  await doorTo(0, 1100); await boltTo(true); log('操作', '施錠', '自動施錠');
}

/* ---------- 四つの場面 ---------- */
async function begin(head) {
  await ensure3D();
  if (inside) await leave();
  hudReset(true, head); go(camTo(xrayOn ? P.xray : P.front, 600));
}
async function approach(msg, sub, fromIssuer) {
  status(msg, '', sub); setLink(1);
  const start = V(-1.05, .95, 1.15);
  phone.position.copy(start); phone.rotation.set(-.25, .35, 0); phone.visible = true;
  if (fromIssuer) await pulse([issuePos, start], 550);
  await tween('scene', 800, k => { phone.position.lerpVectors(start, tapPos, k); phone.rotation.set(lerp(-.25, -.55, k), lerp(.35, 0, k), 0); });
  setRing('run'); go(glow(M.reader, .5));
  await pulse(pathReaderCtrl, 600); setLed('run'); go(glow(M.ctrl, .7));
}
async function check(failAt) {
  for (const li of hud.querySelectorAll('li')) {
    li.dataset.s = 'run'; await wait(230);
    if (li.dataset.k === failAt) { li.dataset.s = 'ng'; return false; }
    li.dataset.s = 'ok';
  }
  return true;
}
async function unlock(color) {
  const lockWorld = lockCase.getWorldPosition(V(0, 0, 0));
  await pulse(pathCtrlHinge.concat([lockWorld]), 600, color); go(glow(M.lock, .9));
  await boltTo(false);
}
async function relock() {
  await doorTo(0, 1200); await boltTo(true); setRing('idle'); setLed('idle'); phone.visible = false;
  for (const m of [M.lock, M.ctrl, M.reader]) go(glow(m, 0, 500));
  log('操作', '施錠', '自動施錠');
}
const scenes = {
  async home() {
    await begin();
    await approach('住人が帰ってきました', '鍵は、管理者が出した署名つきの資格情報です', true);
    await check(); setRing('ok'); setLed('ok');
    await unlock(RING.ok); status('解錠', 'ok', '扉が自分で確かめて、開けました');
    log('認証', '成功', '署名つき資格情報'); log('操作', '解錠', '成功');
    await doorTo(92, 1200); await wait(1900); await relock(); setLink(0);
    status('施錠', '', '閉まると、自動でかかります');
  },
  async offline() {
    await begin(); setNet(false);
    await approach('網が切れています', 'ルーターも、発行する側の機械も、今は届きません', false);
    await check(); setRing('ok'); setLed('ok');
    await unlock(RING.ok); status('解錠', 'ok', '網なしで、扉だけで確かめました');
    log('認証', '成功', '署名つき資格情報'); log('操作', '解錠', '成功');
    await doorTo(92, 1200); await wait(1900); await relock();
    status('施錠', '', '記録は扉に残り、網が戻ると届きます');
  },
  async revoked() {
    await begin();
    await approach('止めた鍵が来ました', '管理者が、この鍵を失効にしてあります', false);
    await check('rev'); setRing('ng'); setLed('ng');
    const x = phone.position.x; await tween('scene', 420, k => { phone.position.x = x + Math.sin(k * Math.PI * 5) * .012 * (1 - k); }, lin);
    status('拒否 失効済み', 'ng', '署名が正しくても、止めた鍵では開きません');
    log('認証', '拒否', '失効済み', 'ng');
    await wait(2200); phone.visible = false; setRing('idle'); setLed('idle'); setLink(0);
    for (const m of [M.ctrl, M.reader]) go(glow(m, 0, 500));
  },
  async fire() {
    await begin('火災の信号'); hud.querySelectorAll('li').forEach(li => { li.hidden = true; });
    $('#alarm').hidden = false; status('火災の信号が入りました', 'warn', '建物の報知器から、扉のコントローラへ');
    log('非常', '火災信号', '入', 'warn');
    await pulse([V(2.1, 2.42, -.2), V(.6, 2.42, -.2), ctrlPos], 700, RING.warn); setLed('warn'); go(glow(M.ctrl, .7));
    await unlock(RING.warn); setRing('warn');
    status('解放', 'warn', '避難のために錠を解放しました。施錠の要求は受けません');
    log('操作', '解錠', '火災信号', 'warn');
    await wait(700); await doorTo(70, 1100); await wait(1800);
    $('#alarm').hidden = true; log('非常', '火災信号', '復旧'); await relock();
    status('復旧', '', '信号が戻ると、通常に戻ります');
  },
};

/* ---------- 操作 ---------- */
for (const k of ['home', 'offline', 'revoked', 'fire']) $('#s-' + k).addEventListener('click', e => play(e.currentTarget, scenes[k]));
$('#b-xray').addEventListener('click', () => {
  const on = !xrayOn;
  play(null, async () => { await ensure3D(); if (inside) await leave(); setXray(on); await camTo(on ? P.xray : P.front, 700); });
});
$('#b-walk').addEventListener('click', () => play(null, async () => { await ensure3D(); if (inside) await leave(); else await enter(); }));
const about = {
  reader: ['ドアホン', '来た人を映し、鍵を読み取ります', M.reader], lock: ['電子錠', 'かんぬきを動かします。物理キーも非常用に残します', M.lock],
  ctrl: ['扉のコントローラ', '開けるかどうかを決め、署名つきで記録します', M.ctrl],
  issue: ['鍵の発行', '建物や住戸の持ち主が、自分の鍵で出します', null], biz: ['業務システムとの連携', '予約や名簿とつなぎ、扉の記録を受け取ります', null],
};
$('#layers').addEventListener('click', e => {
  const b = e.target.closest('[data-layer]'); if (!b) return;
  play(b, async () => {
    await ensure3D(); if (inside) await leave();
    const [name, text, m] = about[b.dataset.layer];
    setXray(true); go(camTo(P.xray, 700)); status(name, '', text);
    if (m) { for (let i = 0; i < 3; i++) { await glow(m, 1, 260); await glow(m, .25, 260); } }
  });
});
const COLORS = [['鉄紺', '#27384f'], ['墨', '#2e3235'], ['深緑', '#2c4a40'], ['煉瓦', '#7b3f30'], ['砂', '#b8ad9a'], ['白磁', '#dfddd6']];
COLORS.forEach(([name, hex], i) => {
  const b = document.createElement('button'); b.style.setProperty('--c', hex); b.title = name; b.setAttribute('aria-label', '扉の色 ' + name); b.setAttribute('aria-pressed', i === 0);
  b.addEventListener('click', () => { M.leaf.color.copy(col(hex)); document.querySelectorAll('#colors button').forEach(x => x.setAttribute('aria-pressed', x === b)); });
  $('#colors').append(b);
});
$('#designs').addEventListener('click', e => {
  const b = e.target.closest('[data-design]'); if (!b) return;
  for (const k in designs) designs[k].visible = k === b.dataset.design;
  document.querySelectorAll('#designs button').forEach(x => x.setAttribute('aria-pressed', x === b));
});
$('#morph').addEventListener('input', e => {
  bump('morph'); const v = e.target.value / 1000;
  if (v < 1 && t >= 1) { bump('scene'); bump('cam'); reset(); setXray(false, 1); xray = 0; inside = false; insideK = 0; cam.free = cam.locked = false; $('#b-walk').textContent = '中に入る'; }
  setT(v);
});

// 回す、寄る
const ptr = new Map(); let pinch = 0;
function limit() {
  if (inside) { o.az = clamp(o.az, Math.PI - .28, Math.PI + .28); o.pol = clamp(o.pol, 1.05, 1.68); o.r = clamp(o.r, .9, 2.7); }
  else { o.az = clamp(o.az, -1.25, 1.25); o.pol = clamp(o.pol, 1.12, 1.58); o.r = clamp(o.r, 1.5, 7); }
}
const canOrbit = () => t >= 1 && !cam.locked && !cam.free;
canvas.addEventListener('pointerdown', e => { ptr.set(e.pointerId, [e.clientX, e.clientY]); canvas.setPointerCapture(e.pointerId); canvas.classList.add('drag'); if (canOrbit()) bump('cam'); });
canvas.addEventListener('pointermove', e => {
  const p = ptr.get(e.pointerId); if (!p) return;
  const dx = e.clientX - p[0], dy = e.clientY - p[1]; p[0] = e.clientX; p[1] = e.clientY;
  if (!canOrbit()) return;
  if (ptr.size === 1) { o.az -= dx * .0055; o.pol -= dy * .0045; }
  else if (ptr.size === 2) { const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch) o.r *= pinch / d; pinch = d; }
  limit();
});
const up = e => { ptr.delete(e.pointerId); pinch = 0; if (!ptr.size) canvas.classList.remove('drag'); };
canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
canvas.addEventListener('wheel', e => { e.preventDefault(); if (!canOrbit()) return; bump('cam'); o.r *= Math.exp(e.deltaY * .0012); limit(); }, { passive: false });

function resize() {
  W = stage.clientWidth || 1; H = stage.clientHeight || 1;
  renderer.setSize(W, H, false); camera.aspect = W / H; camera.updateProjectionMatrix();
  fit = clamp(1.1 / camera.aspect, 1, 2.1);
}
new ResizeObserver(resize).observe(stage); resize();

function frame() {
  stepTweens(performance.now());
  applyMorph(); placeCamera(); renderer.render(scene, camera); placeLabels();
  requestAnimationFrame(frame);
}
applyDoor(); setT(t); frame();
// 開いた時に一度だけ、図面から立体へ
if (!reduce) go(tween('morph', 900, noop, lin, true).then(() => tween('morph', 2800, k => setT(k))));
})();
