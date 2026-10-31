import React, { useState } from 'react';
import type { RoomSnapshotMessage } from '../types/protocol';
import { Arena3D } from './Arena3D';

interface RoomViewProps {
  room: RoomSnapshotMessage;
  sessionId: string;
  assignedRobotId: string | null;
  onToggleReady: (nextReadyState: boolean) => void;
  onLeaveRoom: () => void;
  errorMessage?: string | null;
}

export const RoomView: React.FC<RoomViewProps> = ({
  room,
  sessionId,
  assignedRobotId,
  onToggleReady,
  onLeaveRoom,
  errorMessage,
}) => {
  const [copied, setCopied] = useState(false);

  const myMember = room.members.find((m) => m.session_id === sessionId);
  const isReady = myMember?.is_ready || false;
  const isOwner = myMember?.is_owner || room.owner_id === sessionId;

  const handleCopyCode = async () => {
    try {
      await navigator.clipboard.writeText(room.room_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  return (
    <div className="w-full max-w-6xl mx-auto flex flex-col gap-6">
      {/* Room Header Bar */}
      <div className="bg-slate-900/90 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-2xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-mono uppercase tracking-widest text-cyan-400 font-bold bg-cyan-950/70 px-2.5 py-0.5 rounded border border-cyan-800">
              LOBBY STATUS: {room.status}
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

      {/* Main Content: 3D Visual Arena (Left) and Members / Controls (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 3D Visual Arena Component */}
        <div className="lg:col-span-2 flex flex-col gap-2">
          <Arena3D members={room.members} mySessionId={sessionId} myRobotId={assignedRobotId} />
          <p className="text-[11px] font-mono text-slate-400 px-1">
            Note: Robots are shown on their authoritative starting staging grid. Real-time control & pose synchronization begins in Module 7.
          </p>
        </div>

        {/* Roster & Controls Panel */}
        <div className="flex flex-col gap-6">
          {/* Members Roster Card */}
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
                  return (
                    <div
                      key={member.session_id}
                      className={`p-3 rounded-xl border transition flex items-center justify-between ${
                        isMe
                          ? 'border-cyan-500/80 bg-cyan-950/30'
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
