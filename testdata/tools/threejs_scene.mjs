// Builds the test scene with real three.js classes and writes Object3D.toJSON() (format 4.x).
// Usage: node threejs_scene.mjs out.json
import { writeFileSync } from "node:fs";
import * as THREE from "three";

const scene = new THREE.Scene();
scene.name = "RendaTestScene";

const mat = (name, color, metalness, roughness) =>
  Object.assign(new THREE.MeshStandardMaterial({ color, metalness, roughness }), { name });

const table = new THREE.Group();
table.name = "Table";
const wood = mat("table_wood", 0x73502e, 0, 0.6);
const top = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.1, 1.6), wood);
top.position.y = -0.05;
table.add(top);
for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
  const leg = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.9, 0.1), wood);
  leg.position.set(sx * 1.05, -0.55, sz * 0.65);
  table.add(leg);
}
scene.add(table);

const knot = new THREE.Mesh(new THREE.TorusKnotGeometry(0.25, 0.08, 160, 16), mat("gold_metal", 0xffb84d, 1, 0.25));
knot.name = "TorusKnot";
knot.position.set(0, 0.45, 0);
scene.add(knot);

const sphere = new THREE.Mesh(new THREE.SphereGeometry(0.28, 48, 24), mat("red_plastic", 0xcc140f, 0, 0.35));
sphere.name = "Sphere";
sphere.position.set(0.75, 0.28, -0.25);
scene.add(sphere);

// Checker texture: a DataTexture placeholder; generate.py swaps its image for a PNG data URI.
const size = 64;
const data = new Uint8Array(size * size * 4);
const tex = new THREE.DataTexture(data, size, size);
tex.name = "checker";
tex.colorSpace = THREE.SRGBColorSpace;
const checker = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.5 });
checker.name = "checker";
const cube = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.4, 0.4), checker);
cube.name = "CheckerCube";
cube.position.set(-0.75, 0.2, 0.2);
cube.rotation.y = THREE.MathUtils.degToRad(25);
scene.add(cube);

const cyl = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 0.3, 32), mat("blue_rough", 0x1a40cc, 0, 0.8));
cyl.name = "Cylinder";
cyl.position.set(-0.3, 0.15, -0.5);
scene.add(cyl);

const sun = new THREE.DirectionalLight(0xffffff, 3);
sun.name = "Sun";
sun.position.set(3, 5, 2);
scene.add(sun);
scene.add(Object.assign(new THREE.AmbientLight(0xffffff, 0.3), { name: "Ambient" }));

const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.1, 100);
camera.name = "Camera";
camera.position.set(3.2, 2.0, 3.0);
camera.lookAt(0, 0.3, 0);
scene.add(camera);

// toJSON() writes each object's local .matrix, which only refreshes on render/updateMatrixWorld().
scene.updateMatrixWorld(true);
writeFileSync(process.argv[2], JSON.stringify(scene.toJSON()));
console.log("wrote", process.argv[2]);
