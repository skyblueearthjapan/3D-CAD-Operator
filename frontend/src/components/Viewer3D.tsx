import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface Props {
  glbUrl: string | null;
}

/** 生成ソリッドの 3D プレビュー (three.js) */
export default function Viewer3D({ glbUrl }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    modelGroup: THREE.Group;
  } | null>(null);

  useEffect(() => {
    const mount = mountRef.current!;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf4f7fb);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
    camera.position.set(300, 300, 300);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    // ライティング: CAD らしい柔らかい三点照明
    scene.add(new THREE.HemisphereLight(0xffffff, 0xdde6f0, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, 2, 1.5);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x88b8ff, 0.5);
    rim.position.set(-2, 0.6, -1);
    scene.add(rim);

    const grid = new THREE.GridHelper(1000, 50, 0xa8c0dc, 0xdbe6f2);
    scene.add(grid);
    const axes = new THREE.AxesHelper(80);
    scene.add(axes);

    const modelGroup = new THREE.Group();
    scene.add(modelGroup);

    stateRef.current = { renderer, scene, camera, controls, modelGroup };

    const onResize = () => {
      const w = mount.clientWidth, h = mount.clientHeight;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    onResize();
    const ro = new ResizeObserver(onResize);
    ro.observe(mount);

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, camera);
    };
    loop();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      stateRef.current = null;
    };
  }, []);

  useEffect(() => {
    const st = stateRef.current;
    if (!st) return;
    st.modelGroup.clear();
    if (!glbUrl) return;

    const loader = new GLTFLoader();
    loader.load(glbUrl, (gltf) => {
      const st2 = stateRef.current;
      if (!st2) return;
      const material = new THREE.MeshStandardMaterial({
        color: 0x8fa8c4, metalness: 0.45, roughness: 0.45,
        side: THREE.DoubleSide,
      });
      const group = new THREE.Group();
      gltf.scene.traverse((obj) => {
        if ((obj as THREE.Mesh).isMesh) {
          const mesh = obj as THREE.Mesh;
          const m = new THREE.Mesh(mesh.geometry, material);
          m.applyMatrix4(mesh.matrixWorld);
          group.add(m);
          const edges = new THREE.EdgesGeometry(mesh.geometry, 25);
          const line = new THREE.LineSegments(
            edges, new THREE.LineBasicMaterial({ color: 0x33507a }));
          line.applyMatrix4(mesh.matrixWorld);
          group.add(line);
        }
      });
      // 原点合わせ & カメラフィット
      const box = new THREE.Box3().setFromObject(group);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      group.position.sub(center);
      st2.modelGroup.add(group);

      const d = Math.max(size.x, size.y, size.z) * 1.6 + 1;
      st2.camera.position.set(d, d * 0.8, d);
      st2.camera.near = d / 1000;
      st2.camera.far = d * 100;
      st2.camera.updateProjectionMatrix();
      st2.controls.target.set(0, 0, 0);
      st2.controls.update();
    });
  }, [glbUrl]);

  return (
    <div className="viewer3d" ref={mountRef}>
      {!glbUrl && (
        <div className="viewer3d-empty">
          <div className="empty-icon">◇</div>
          <p>外形と板厚を設定して「3Dモデル生成」を押すと<br />ここにプレビューが表示されます</p>
        </div>
      )}
    </div>
  );
}
