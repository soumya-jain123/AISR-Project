import React, { useEffect, useRef, useState } from 'react';
import type { ControlState, RobotStatePayload, RoomSnapshotMessage } from '../types/protocol';
import { Arena3D } from './Arena3D';

interface RoomViewProps {
  room: RoomSnapshotMessage;
  sessionId: string;
  assignedRobotId: string | null;
  robotStates?: RobotStatePayload[];
  onToggleReady: (nextReadyState: boolean) => void;
  onLeaveRoom: () => void;
  onSendInput?: (throttle: number, steering: number, controlState: ControlState) => void;
  onSendStop?: () => void;
  onSendEstop?: () => void;
  errorMessage?: string | null;
}

export const RoomView: React.FC<RoomViewProps> = ({
  room,
  sessionId,
  assignedRobotId,
  robotStates = [],
  onToggleReady,
  onLeaveRoom,
  onSendInput,
  onSendStop,
  onSendEstop,
  errorMessage,
}) => {
  const [copied, setCopied] = useState(false);
  const [liveThrottle, setLiveThrottle] = useState<number>(0.0);
  const [liveSteering, setLiveSteering] = useState<number>(0.0);
  const pressedKeysRef = useRef<Set<string>>(new Set());

  const myMember = room.members.find((m) => m.session_id === sessionId);
  const isReady = myMember?.is_ready || false;
  const isOwner = myMember?.is_owner || room.owner_id === sessionId;

  // Find telemetry for user's assigned robot if available
  const myRobotTelemetry = assignedRobotId
    ? robotStates.find((r) => r.robot_id === assignedRobotId)
    : null;

  const handleCopyCode = async () => {
    try {
      await navigator.clipboard.writeText(room.room_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  // Keyboard driving controls with focus/blur safety and scroll prevention
  useEffect(() => {
    if (!assignedRobotId) return;

    const computeAndDispatch = (stateOverride?: ControlState) => {
      const keys = pressedKeysRef.current;
      const forward = keys.has('KeyW') || keys.has('ArrowUp');
      const reverse = keys.has('KeyS') || keys.has('ArrowDown');
      const left = keys.has('KeyA') || keys.has('ArrowLeft');
      const right = keys.has('KeyD') || keys.has('ArrowRight');

      let th = 0.0;
      if (forward && !reverse) th = 1.0;
      else if (reverse && !forward) th = -1.0;

      let st = 0.0;
      if (left && !right) st = -1.0;
      else if (right && !left) st = 1.0;

      const controlState: ControlState =
        stateOverride ||
        (keys.size === 0 || (th === 0 && st === 0) ? 'neutral' : 'active');

      setLiveThrottle(th);
      setLiveSteering(st);
      onSendInput?.(th, st, controlState);
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore key events when user is typing in text fields
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      // Prevent window scroll on driving arrow keys or spacebar
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(e.key)) {
        e.preventDefault();
      }

      // Handle emergency stop key
      if (e.code === 'KeyE') {
        onSendEstop?.();
        return;
      }

      // Handle spacebar brake / normal stop
      if (e.code === 'Space') {
        pressedKeysRef.current.clear();
        computeAndDispatch('neutral');
        onSendStop?.();
        return;
      }

      const relevantCodes = ['KeyW', 'KeyS', 'KeyA', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
      if (relevantCodes.includes(e.code) && !pressedKeysRef.current.has(e.code)) {
        pressedKeysRef.current.add(e.code);
        computeAndDispatch('active');
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      const relevantCodes = ['KeyW', 'KeyS', 'KeyA', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
      if (relevantCodes.includes(e.code)) {
        pressedKeysRef.current.delete(e.code);
        computeAndDispatch(pressedKeysRef.current.size === 0 ? 'released' : 'active');
      }
    };

    const handleBlur = () => {
      // Neutralize input immediately when browser window loses focus
      if (pressedKeysRef.current.size > 0) {
        pressedKeysRef.current.clear();
        setLiveThrottle(0.0);
        setLiveSteering(0.0);
        onSendInput?.(0.0, 0.0, 'released');
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    window.addEventListener('blur', handleBlur);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
      window.removeEventListener('blur', handleBlur);
      // Clean up on unmount
      onSendInput?.(0.0, 0.0, 'released');
    };
  }, [assignedRobotId, onSendInput, onSendStop, onSendEstop]);

  return (
    <div className="w-full max-w-6xl mx-auto flex flex-col gap-6">
      {/* Room Header Bar */}
      <div className="bg-slate-900/90 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-2xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono uppercase tracking-widest text-cyan-400 font-bold bg-cyan-950/70 px-2.5 py-0.5 rounded border border-cyan-800">
              ROOM STATUS: {room.status}
            </span>
            <span className="text-xs font-mono text-slate-400">
              Capacity: {room.members.length} / {room.capacity}
            </span>
          </div>
          <h1 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">{room.name}</h1>
        </div>

        {/* Room Code Badge with Copy */}
        <div className="flex items-center gap-3 bg-slate-950 border border-slate-700 p-2.5 rounded-xl shadow-inner">
          <div className="flex flex-col text-right">
            <span className="text-[10px] font-mono text-slate-400 uppercase tracking-widest">Share Room Code</span>
            <span className="text-2xl font-mono font-black text-cyan-300 tracking-wider">{room.room_code}</span>
          </div>
          <button
            onClick={handleCopyCode}
            title="Copy room code to clipboard"
            className="bg-slate-800 hover:bg-slate-700 text-cyan-400 px-3 py-2 rounded-lg text-xs font-mono transition flex items-center gap-1 active:scale-95"
          >
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* Error Alert if any */}
      {errorMessage && (
        <div className="bg-red-950/80 border border-red-500/80 text-red-200 px-4 py-3 rounded-xl text-sm font-mono shadow-lg flex items-center gap-2">
          <span>⚠</span>
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Main Content: 3D Visual Arena (Left) and Members / Driving Controls (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 3D Visual Arena Component */}
        <div className="lg:col-span-2 flex flex-col gap-3">
          <Arena3D
            members={room.members}
            mySessionId={sessionId}
            myRobotId={assignedRobotId}
            robotStates={robotStates}
          />

          {/* Interactive Cockpit / Driving HUD */}
          {assignedRobotId ? (
            <div className="bg-slate-900/90 backdrop-blur-md rounded-2xl p-4 border border-slate-700/80 shadow-xl flex flex-col gap-3">
              <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
                  <span className="text-sm font-bold font-mono text-white">
                    DRIVING COCKPIT: <span className="text-cyan-300">{assignedRobotId}</span>
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-slate-400">Status:</span>
                  <span
                    className={`text-xs font-mono font-bold px-2 py-0.5 rounded uppercase ${
                      myRobotTelemetry?.state === 'ESTOP_LATCHED'
                        ? 'bg-red-950 text-red-400 border border-red-700'
                        : myRobotTelemetry?.state === 'COMM_LOST'
                        ? 'bg-amber-950 text-amber-400 border border-amber-700'
                        : myRobotTelemetry?.state === 'MOVING'
                        ? 'bg-emerald-950 text-emerald-300 border border-emerald-700'
                        : 'bg-slate-800 text-slate-300'
                    }`}
                  >
                    {myRobotTelemetry?.state || 'IDLE'}
                  </span>
                </div>
              </div>

              {/* Gauges & Keyboard Indicators */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {/* Throttle Gauge */}
                <div className="bg-slate-950 p-2.5 rounded-xl border border-slate-800 flex flex-col justify-between">
                  <span className="text-[10px] font-mono text-slate-400 uppercase">Throttle</span>
                  <div className="text-lg font-mono font-bold text-cyan-300">
                    {Math.round(liveThrottle * 100)}%
                  </div>
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden mt-1">
                    <div
                      className="bg-cyan-400 h-full transition-all duration-75"
                      style={{
                        width: `${Math.abs(liveThrottle) * 50}%`,
                        marginLeft: liveThrottle < 0 ? `${50 - Math.abs(liveThrottle) * 50}%` : '50%',
                      }}
                    />
                  </div>
                </div>

                {/* Steering Gauge */}
                <div className="bg-slate-950 p-2.5 rounded-xl border border-slate-800 flex flex-col justify-between">
                  <span className="text-[10px] font-mono text-slate-400 uppercase">Steering</span>
                  <div className="text-lg font-mono font-bold text-cyan-300">
                    {Math.round(liveSteering * 100)}%
                  </div>
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden mt-1">
                    <div
                      className="bg-amber-400 h-full transition-all duration-75"
                      style={{
                        width: `${Math.abs(liveSteering) * 50}%`,
                        marginLeft: liveSteering < 0 ? `${50 - Math.abs(liveSteering) * 50}%` : '50%',
                      }}
                    />
                  </div>
                </div>

                {/* Speed Telemetry */}
                <div className="bg-slate-950 p-2.5 rounded-xl border border-slate-800 flex flex-col justify-between">
                  <span className="text-[10px] font-mono text-slate-400 uppercase">Velocity</span>
                  <div className="text-base font-mono font-bold text-white">
                    {myRobotTelemetry ? `${myRobotTelemetry.linear_vel.toFixed(2)} m/s` : '0.00 m/s'}
                  </div>
                  <span className="text-[10px] font-mono text-slate-500">
                    Yaw: {myRobotTelemetry ? `${(myRobotTelemetry.theta * 57.2958).toFixed(1)}°` : '0.0°'}
                  </span>
                </div>

                {/* Action Buttons: Brake and E-Stop */}
                <div className="flex flex-col gap-1.5 justify-center">
                  <button
                    onClick={() => onSendStop?.()}
                    className="w-full py-1.5 bg-slate-800 hover:bg-slate-700 active:bg-slate-600 text-amber-300 border border-amber-600/40 rounded-lg text-xs font-mono font-bold transition flex items-center justify-center gap-1"
                  >
                    Brake [Space]
                  </button>
                  <button
                    onClick={() => onSendEstop?.()}
                    className="w-full py-1.5 bg-red-950/80 hover:bg-red-900 active:bg-red-800 text-red-200 border border-red-700 rounded-lg text-xs font-mono font-bold transition flex items-center justify-center gap-1 shadow-sm"
                  >
                    ⚠ E-STOP [E]
                  </button>
                </div>
              </div>

              {/* Driving Controls Guide */}
              <div className="text-[11px] font-mono text-slate-400 bg-slate-950/60 p-2 rounded-lg border border-slate-800 flex items-center justify-between flex-wrap gap-2">
                <span>Driving: <kbd className="bg-slate-800 text-cyan-300 px-1 py-0.5 rounded border border-slate-700">W/S/A/D</kbd> or <kbd className="bg-slate-800 text-cyan-300 px-1 py-0.5 rounded border border-slate-700">Arrow Keys</kbd></span>
                <span>Brake: <kbd className="bg-slate-800 text-amber-300 px-1 py-0.5 rounded border border-slate-700">Space</kbd></span>
                <span>Emergency Stop: <kbd className="bg-slate-800 text-red-400 px-1 py-0.5 rounded border border-slate-700">E</kbd></span>
              </div>
            </div>
          ) : (
            <div className="bg-slate-900/60 p-3 rounded-xl border border-slate-800 text-xs font-mono text-slate-400">
              Spectator Mode: No robot assigned. You have viewing privileges for this race room.
            </div>
          )}
        </div>

        {/* Roster & Room Action Controls Panel */}
        <div className="flex flex-col gap-6">
          <div className="bg-slate-900/80 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-xl flex-1 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-4 pb-2 border-b border-slate-800">
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <span className="w-1.5 h-4 bg-cyan-400 rounded-full" />
                  Grid Racers ({room.members.length}/{room.capacity})
                </h2>
                {isOwner && (
                  <span className="text-[10px] font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40 px-2 py-0.5 rounded">
                    ★ Host
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-2.5">
                {room.members.map((member) => {
                  const isMe = member.session_id === sessionId;
                  const memberRobot = robotStates.find((r) => r.robot_id === member.robot_id);
                  return (
                    <div
                      key={member.session_id}
                      className={`p-3 rounded-xl border transition flex items-center justify-between ${
                        isMe
                          ? 'border-cyan-500/80 bg-cyan-950/30 ring-1 ring-cyan-500/40'
                          : 'border-slate-800 bg-slate-950/50'
                      }`}
                    >
                      <div className="flex flex-col">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-white text-sm">
                            {member.display_name}
                          </span>
                          {member.is_owner && (
                            <span className="text-[10px] font-mono text-amber-400">★</span>
                          )}
                          {isMe && (
                            <span className="text-[9px] font-mono font-bold bg-cyan-400 text-slate-950 px-1 rounded">
                              YOU
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5 text-xs font-mono text-slate-400">
                          <span>Role: {member.role}</span>
                          {member.robot_id && (
                            <span className="text-cyan-300 font-bold">• {member.robot_id}</span>
                          )}
                          {memberRobot && (
                            <span
                              className={`text-[9px] px-1 rounded ${
                                memberRobot.state === 'MOVING'
                                  ? 'bg-emerald-950 text-emerald-400'
                                  : memberRobot.state === 'ESTOP_LATCHED'
                                  ? 'bg-red-950 text-red-400'
                                  : 'text-slate-500'
                              }`}
                            >
                              {memberRobot.state}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Ready Badge */}
                      <div>
                        {member.is_ready ? (
                          <span className="inline-flex items-center gap-1 text-xs font-mono font-bold text-emerald-400 bg-emerald-950/60 border border-emerald-800 px-2 py-1 rounded-md">
                            ✓ READY
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs font-mono text-slate-400 bg-slate-800 px-2 py-1 rounded-md">
                            ⏳ WAITING
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Bottom Player Action Controls */}
            <div className="pt-6 border-t border-slate-800 flex flex-col gap-3">
              <button
                onClick={() => onToggleReady(!isReady)}
                className={`w-full py-3.5 rounded-xl font-bold font-mono uppercase tracking-wider text-sm transition shadow-lg flex items-center justify-center gap-2 ${
                  isReady
                    ? 'bg-amber-500 hover:bg-amber-400 text-slate-950 ring-2 ring-amber-400/50'
                    : 'bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950'
                }`}
              >
                {isReady ? 'Cancel Ready (Unready)' : 'Ready Up for Race'}
              </button>

              <button
                onClick={onLeaveRoom}
                className="w-full py-2.5 rounded-xl text-xs font-mono font-semibold text-red-400 hover:text-red-300 bg-red-950/30 hover:bg-red-950/60 border border-red-900/60 transition"
              >
                Leave Race Room
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
