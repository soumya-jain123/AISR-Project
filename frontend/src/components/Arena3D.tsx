import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { RobotStatePayload, RoomMemberPayload } from '../types/protocol';

interface Arena3DProps {
  members?: RoomMemberPayload[];
  mySessionId?: string | null;
  myRobotId?: string | null;
  robotStates?: RobotStatePayload[];
}

interface RobotVisualConfig {
  id: string;
  name: string;
  color: number;
  hexColor: string;
  initialPose: [number, number, number]; // (x, y, theta)
}

const DEFAULT_ROBOTS: RobotVisualConfig[] = [
  {
    id: 'robot_1',
    name: 'Pioneer Racer 1',
    color: 0xef4444, // Red
    hexColor: '#ef4444',
    initialPose: [0.0, 0.0, 0.0],
  },
  {
    id: 'robot_2',
    name: 'Pioneer Racer 2',
    color: 0x06b6d4, // Cyan
    hexColor: '#06b6d4',
    initialPose: [0.0, 1.2, 0.0],
  },
  {
    id: 'robot_3',
    name: 'Pioneer Racer 3',
    color: 0x10b981, // Emerald
    hexColor: '#10b981',
    initialPose: [-1.8, 0.0, 0.0],
  },
  {
    id: 'robot_4',
    name: 'Pioneer Racer 4',
    color: 0xf59e0b, // Amber / Gold
    hexColor: '#f59e0b',
    initialPose: [-1.8, 1.2, 0.0],
  },
];

export const Arena3D: React.FC<Arena3DProps> = ({
  members = [],
  mySessionId,
  myRobotId,
  robotStates = [],
}) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const [webGLSupported, setWebGLSupported] = useState<boolean>(true);
  const [cameraMode, setCameraMode] = useState<'perspective' | 'top'>('perspective');

  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const robotMeshesRef = useRef<Map<string, THREE.Group>>(new Map());
  const targetsRef = useRef<Map<string, RobotStatePayload>>(new Map());

  // Update target poses when authoritative broadcast arrives
  useEffect(() => {
    if (robotStates && robotStates.length > 0) {
      robotStates.forEach((st) => {
        targetsRef.current.set(st.robot_id, st);
      });
    }
  }, [robotStates]);

  const hasLiveTelemetry = (robotStates && robotStates.length > 0);

  // Check WebGL support on mount
  useEffect(() => {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) {
        setWebGLSupported(false);
      }
    } catch {
      setWebGLSupported(false);
    }
  }, []);

  useEffect(() => {
    if (!webGLSupported || !mountRef.current) return;

    const container = mountRef.current;
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 500;

    // 1. Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e17);
    scene.fog = new THREE.FogExp2(0x0a0e17, 0.035);
    sceneRef.current = scene;

    // 2. Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, 10, 12);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

    // 3. Renderer with graceful WebGL fallback
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    } catch {
      setWebGLSupported(false);
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.innerHTML = '';
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;


    // 4. Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.8);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(10, 20, 10);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.width = 1024;
    dirLight.shadow.mapSize.height = 1024;
    scene.add(dirLight);

    // Accent stadium floodlights
    const cyanLight = new THREE.PointLight(0x00f0ff, 1.5, 30);
    cyanLight.position.set(-10, 6, -10);
    scene.add(cyanLight);

    const orangeLight = new THREE.PointLight(0xff8400, 1.5, 30);
    orangeLight.position.set(10, 6, 10);
    scene.add(orangeLight);

    // 5. Race Track and Floor
    buildTrack(scene);

    // 6. Build Pioneer P3-DX Robot Meshes
    DEFAULT_ROBOTS.forEach((cfg) => {
      const robotGroup = createPioneerRobotMesh(cfg);
      // Coordinate mapping: sim X -> 3D X, sim Y -> 3D -Z, 3D Y is up
      robotGroup.position.set(cfg.initialPose[0] * 3, 0.25, -cfg.initialPose[1] * 3);
      robotGroup.rotation.y = cfg.initialPose[2];
      scene.add(robotGroup);
      robotMeshesRef.current.set(cfg.id, robotGroup);
    });

    // 7. Mouse Orbit Drag Controls
    let isDragging = false;
    let previousMousePosition = { x: 0, y: 0 };
    let cameraAngle = 0;
    let cameraPitch = 0.7;
    const cameraRadius = 16;

    const updateCameraPos = () => {
      if (cameraMode === 'top') {
        camera.position.set(0, 18, 0.1);
        camera.lookAt(0, 0, 0);
      } else {
        camera.position.x = cameraRadius * Math.sin(cameraAngle) * Math.cos(cameraPitch);
        camera.position.z = cameraRadius * Math.cos(cameraAngle) * Math.cos(cameraPitch);
        camera.position.y = cameraRadius * Math.sin(cameraPitch);
        camera.lookAt(0, 0, 0);
      }
    };
    updateCameraPos();

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging || cameraMode === 'top') return;
      const deltaX = e.clientX - previousMousePosition.x;
      const deltaY = e.clientY - previousMousePosition.y;

      cameraAngle -= deltaX * 0.008;
      cameraPitch = Math.max(0.2, Math.min(1.4, cameraPitch + deltaY * 0.008));

      updateCameraPos();
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onMouseUp = () => {
      isDragging = false;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (cameraMode === 'top') return;
      // Zoom
      camera.fov = Math.max(25, Math.min(75, camera.fov + e.deltaY * 0.04));
      camera.updateProjectionMatrix();
    };

    const dom = renderer.domElement;
    dom.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    dom.addEventListener('wheel', onWheel, { passive: false });

    // 8. Resize Handler
    const handleResize = () => {
      if (!container || !renderer || !camera) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);

    // 9. Animation Loop
    let animId: number;
    const clock = new THREE.Clock();

    const animate = () => {
      animId = requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();

      // Smooth interpolation towards authoritative poses
      robotMeshesRef.current.forEach((mesh, rId) => {
        const beacon = mesh.getObjectByName('beacon');
        if (beacon) {
          const isMyBot = rId === myRobotId;
          const scale = isMyBot ? 1.0 + 0.2 * Math.sin(elapsed * 6) : 1.0;
          beacon.scale.set(scale, scale, scale);
        }

        const target = targetsRef.current.get(rId);
        if (target) {
          const targetX = target.x * 3;
          const targetZ = -target.y * 3;
          // Position lerp
          mesh.position.x += (targetX - mesh.position.x) * 0.25;
          mesh.position.z += (targetZ - mesh.position.z) * 0.25;

          // Shortest angle rotation lerp
          let diff = target.theta - mesh.rotation.y;
          while (diff > Math.PI) diff -= 2 * Math.PI;
          while (diff < -Math.PI) diff += 2 * Math.PI;
          mesh.rotation.y += diff * 0.25;
        }
      });

      renderer.render(scene, camera);
    };
    animate();

    return () => {
      cancelAnimationFrame(animId);
      dom.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      dom.removeEventListener('wheel', onWheel);
      window.removeEventListener('resize', handleResize);
      renderer.dispose();
      if (container.contains(dom)) {
        container.removeChild(dom);
      }
    };
  }, [webGLSupported, cameraMode, myRobotId]);

  return (
    <div className="relative w-full h-[450px] md:h-[520px] rounded-xl overflow-hidden border border-slate-700/80 bg-slate-950 shadow-2xl">
      {/* 3D WebGL Canvas */}
      {webGLSupported ? (
        <div ref={mountRef} className="w-full h-full cursor-grab active:cursor-grabbing" />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center p-6 text-center text-slate-400">
          <p className="text-lg font-semibold text-amber-400 mb-2">WebGL Acceleration Unavailable</p>
          <p className="text-sm">Hardware acceleration is disabled or unsupported in this browser environment.</p>
        </div>
      )}

      {/* Camera Mode Toggle Overlay */}
      <div className="absolute top-3 right-3 flex items-center gap-1.5 bg-slate-900/85 backdrop-blur-md p-1.5 rounded-lg border border-slate-700 text-xs font-mono">
        <button
          onClick={() => setCameraMode('perspective')}
          className={`px-2.5 py-1 rounded transition ${
            cameraMode === 'perspective'
              ? 'bg-cyan-500 text-slate-950 font-bold'
              : 'text-slate-300 hover:bg-slate-800'
          }`}
        >
          Orbit 3D
        </button>
        <button
          onClick={() => setCameraMode('top')}
          className={`px-2.5 py-1 rounded transition ${
            cameraMode === 'top'
              ? 'bg-cyan-500 text-slate-950 font-bold'
              : 'text-slate-300 hover:bg-slate-800'
          }`}
        >
          Top Down
        </button>
      </div>

      {/* Grid Staging / Live Synchronization Status Banner */}
      <div className="absolute top-3 left-3 bg-slate-900/85 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700 text-xs font-mono text-slate-300 flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full ${
            hasLiveTelemetry ? 'bg-cyan-400 animate-ping' : 'bg-emerald-400 animate-pulse'
          }`}
        />
        <span>
          {hasLiveTelemetry
            ? 'Authoritative Live State Sync (20 Hz)'
            : 'Staging Grid (Waiting for Live Sync)'}
        </span>
      </div>

      {/* Bottom Telemetry Legend & Assigned Robot HUD */}
      <div className="absolute bottom-3 left-3 right-3 flex flex-wrap items-center justify-between gap-2 bg-slate-900/90 backdrop-blur-md px-4 py-2.5 rounded-lg border border-slate-700 text-xs font-mono">
        <div className="flex items-center gap-4 flex-wrap">
          {DEFAULT_ROBOTS.map((bot) => {
            const member = members.find((m) => m.robot_id === bot.id);
            const targetState = targetsRef.current.get(bot.id);
            const isMe = bot.id === myRobotId;
            return (
              <div
                key={bot.id}
                className={`flex items-center gap-2 px-2.5 py-1 rounded border transition ${
                  isMe
                    ? 'border-cyan-400 bg-cyan-950/40 text-cyan-200 ring-1 ring-cyan-400'
                    : 'border-slate-800 bg-slate-800/60 text-slate-300'
                }`}
              >
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: bot.hexColor }} />
                <span className="font-bold">{bot.id}</span>
                {targetState && (
                  <span
                    className={`text-[9px] px-1 py-0.5 rounded uppercase font-bold ${
                      targetState.state === 'ESTOP_LATCHED'
                        ? 'bg-red-950 text-red-400 border border-red-800'
                        : targetState.state === 'COMM_LOST'
                        ? 'bg-amber-950 text-amber-400 border border-amber-800'
                        : targetState.state === 'MOVING'
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {targetState.state}
                  </span>
                )}
                <span className="text-[11px] text-slate-400">
                  {member ? `[${member.display_name}${member.is_ready ? ' ✓' : ''}]` : '(Available)'}
                </span>
                {isMe && <span className="bg-cyan-400 text-slate-950 font-bold px-1 rounded text-[10px]">YOU</span>}
              </div>
            );
          })}
        </div>
        <div className="text-slate-400 text-[11px] hidden sm:block">
          Controls: Left-drag to orbit • Scroll to zoom
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Three.js Scene Building Helpers
// ---------------------------------------------------------------------------

function buildTrack(scene: THREE.Scene) {
  // Ground plane
  const floorGeo = new THREE.PlaneGeometry(36, 28);
  const floorMat = new THREE.MeshStandardMaterial({
    color: 0x111622,
    roughness: 0.9,
    metalness: 0.1,
  });
  const floor = new THREE.Mesh(floorGeo, floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);

  // Outer track boundary curve
  const trackGeo = new THREE.RingGeometry(5.5, 12.0, 48);
  const trackMat = new THREE.MeshStandardMaterial({
    color: 0x1e2433,
    roughness: 0.7,
    metalness: 0.2,
  });
  const track = new THREE.Mesh(trackGeo, trackMat);
  track.rotation.x = -Math.PI / 2;
  track.position.y = 0.01;
  track.receiveShadow = true;
  scene.add(track);

  // Track Centerline (Dashed Ring)
  const centerGeo = new THREE.RingGeometry(8.7, 8.8, 64);
  const centerMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.35 });
  const centerLine = new THREE.Mesh(centerGeo, centerMat);
  centerLine.rotation.x = -Math.PI / 2;
  centerLine.position.y = 0.02;
  scene.add(centerLine);

  // Inner Island
  const islandGeo = new THREE.CircleGeometry(5.4, 48);
  const islandMat = new THREE.MeshStandardMaterial({
    color: 0x0c1017,
    roughness: 0.9,
  });
  const island = new THREE.Mesh(islandGeo, islandMat);
  island.rotation.x = -Math.PI / 2;
  island.position.y = 0.02;
  scene.add(island);

  // Start / Finish Line Checkered Strip
  const finishGeo = new THREE.PlaneGeometry(1.2, 6.5);
  const finishMat = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.4,
  });
  const finishLine = new THREE.Mesh(finishGeo, finishMat);
  finishLine.rotation.x = -Math.PI / 2;
  finishLine.position.set(0, 0.025, 0);
  scene.add(finishLine);

  // Grid Staging Markers (Boxes on floor)
  DEFAULT_ROBOTS.forEach((bot, idx) => {
    const gridMarkerGeo = new THREE.RingGeometry(0.5, 0.58, 24);
    const gridMarkerMat = new THREE.MeshBasicMaterial({
      color: bot.color,
      transparent: true,
      opacity: 0.6,
    });
    const marker = new THREE.Mesh(gridMarkerGeo, gridMarkerMat);
    marker.rotation.x = -Math.PI / 2;
    marker.position.set(bot.initialPose[0] * 3, 0.03, -bot.initialPose[1] * 3);
    scene.add(marker);
  });

  // Track Outer Wall Guardrails
  const wallGeo = new THREE.TorusGeometry(12.1, 0.12, 8, 48);
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x00f0ff,
    emissive: 0x004455,
    metalness: 0.8,
    roughness: 0.2,
  });
  const outerWall = new THREE.Mesh(wallGeo, wallMat);
  outerWall.rotation.x = Math.PI / 2;
  outerWall.position.y = 0.2;
  scene.add(outerWall);

  const innerWallGeo = new THREE.TorusGeometry(5.4, 0.12, 8, 48);
  const innerWallMat = new THREE.MeshStandardMaterial({
    color: 0xff8400,
    emissive: 0x442200,
    metalness: 0.8,
    roughness: 0.2,
  });
  const innerWall = new THREE.Mesh(innerWallGeo, innerWallMat);
  innerWall.rotation.x = Math.PI / 2;
  innerWall.position.y = 0.2;
  scene.add(innerWall);
}

function createPioneerRobotMesh(cfg: RobotVisualConfig): THREE.Group {
  const group = new THREE.Group();
  group.name = cfg.id;

  // 1. Pioneer P3-DX Chassis Body (Red / Cyan / Emerald / Gold)
  const bodyGeo = new THREE.BoxGeometry(0.7, 0.35, 0.9);
  const bodyMat = new THREE.MeshStandardMaterial({
    color: cfg.color,
    roughness: 0.3,
    metalness: 0.4,
  });
  const body = new THREE.Mesh(bodyGeo, bodyMat);
  body.position.y = 0.2;
  body.castShadow = true;
  group.add(body);

  // 2. Rounded Front Nose (Pioneer characteristic curved front)
  const noseGeo = new THREE.CylinderGeometry(0.35, 0.35, 0.35, 16, 1, false, 0, Math.PI);
  const nose = new THREE.Mesh(noseGeo, bodyMat);
  nose.rotation.z = Math.PI / 2;
  nose.rotation.y = -Math.PI / 2;
  nose.position.set(0, 0.2, -0.45);
  nose.castShadow = true;
  group.add(nose);

  // 3. Sonar Ring Strip (Dark metallic front sensor)
  const sonarGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.08, 16, 1, false, 0, Math.PI);
  const sonarMat = new THREE.MeshStandardMaterial({ color: 0x111111, metalness: 0.9 });
  const sonar = new THREE.Mesh(sonarGeo, sonarMat);
  sonar.rotation.z = Math.PI / 2;
  sonar.rotation.y = -Math.PI / 2;
  sonar.position.set(0, 0.22, -0.46);
  group.add(sonar);

  // 4. Differential Drive Wheels (Left & Right)
  const wheelGeo = new THREE.CylinderGeometry(0.2, 0.2, 0.1, 16);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.8 });
  const hubMat = new THREE.MeshStandardMaterial({ color: 0xcccccc, metalness: 0.8 });

  // Left Wheel
  const leftWheel = new THREE.Mesh(wheelGeo, wheelMat);
  leftWheel.rotation.z = Math.PI / 2;
  leftWheel.position.set(-0.4, 0.2, 0.1);
  leftWheel.castShadow = true;
  group.add(leftWheel);

  const leftHub = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.11, 12), hubMat);
  leftHub.rotation.z = Math.PI / 2;
  leftHub.position.set(-0.4, 0.2, 0.1);
  group.add(leftHub);

  // Right Wheel
  const rightWheel = new THREE.Mesh(wheelGeo, wheelMat);
  rightWheel.rotation.z = Math.PI / 2;
  rightWheel.position.set(0.4, 0.2, 0.1);
  rightWheel.castShadow = true;
  group.add(rightWheel);

  const rightHub = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.11, 12), hubMat);
  rightHub.rotation.z = Math.PI / 2;
  rightHub.position.set(0.4, 0.2, 0.1);
  group.add(rightHub);

  // 5. Rear Castor Wheel
  const castorGeo = new THREE.SphereGeometry(0.09, 12, 12);
  const castor = new THREE.Mesh(castorGeo, wheelMat);
  castor.position.set(0, 0.09, 0.35);
  castor.castShadow = true;
  group.add(castor);

  // 6. Antenna / Status Beacon
  const mastGeo = new THREE.CylinderGeometry(0.015, 0.015, 0.45, 8);
  const mastMat = new THREE.MeshStandardMaterial({ color: 0x888888, metalness: 0.9 });
  const mast = new THREE.Mesh(mastGeo, mastMat);
  mast.position.set(0.2, 0.58, 0.25);
  group.add(mast);

  const beaconGeo = new THREE.SphereGeometry(0.06, 12, 12);
  const beaconMat = new THREE.MeshStandardMaterial({
    color: cfg.color,
    emissive: cfg.color,
    emissiveIntensity: 0.8,
  });
  const beacon = new THREE.Mesh(beaconGeo, beaconMat);
  beacon.name = 'beacon';
  beacon.position.set(0.2, 0.82, 0.25);
  group.add(beacon);

  return group;
}
