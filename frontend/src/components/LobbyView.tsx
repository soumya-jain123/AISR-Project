import React, { useState } from 'react';
import type { ConnectionStatus, RoomSummaryPayload } from '../types/protocol';

interface LobbyViewProps {
  connectionStatus: ConnectionStatus;
  displayName: string;
  onDisplayNameChange: (name: string) => void;
  onCreateRoom: (name: string, capacity: number, role: 'driver' | 'spectator') => void;
  onJoinRoom: (code: string, role: 'driver' | 'spectator') => void;
  onRefreshRooms: () => void;
  activeRooms: RoomSummaryPayload[];
  errorMessage?: string | null;
  isBusy?: boolean;
}

export const LobbyView: React.FC<LobbyViewProps> = ({
  connectionStatus,
  displayName,
  onDisplayNameChange,
  onCreateRoom,
  onJoinRoom,
  onRefreshRooms,
  activeRooms,
  errorMessage,
  isBusy = false,
}) => {
  const [roomCodeInput, setRoomCodeInput] = useState('');
  const [createRoomName, setCreateRoomName] = useState('Pioneer Circuit 1');
  const [createCapacity, setCreateCapacity] = useState<number>(4);
  const [createRole, setCreateRole] = useState<'driver' | 'spectator'>('driver');
  const [joinRole, setJoinRole] = useState<'driver' | 'spectator'>('driver');

  const isValidDisplayName =
    displayName.trim().length >= 1 &&
    displayName.trim().length <= 32 &&
    /^[a-zA-Z0-9 _\-]+$/.test(displayName.trim());

  const isValidCode = roomCodeInput.trim().length >= 4 && roomCodeInput.trim().length <= 16;

  const handleCreateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValidDisplayName || isBusy) return;
    onCreateRoom(createRoomName.trim(), createCapacity, createRole);
  };

  const handleJoinSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValidDisplayName || !isValidCode || isBusy) return;
    onJoinRoom(roomCodeInput.trim().toUpperCase(), joinRole);
  };

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-6">
      {/* Hero Header */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-slate-900 via-slate-850 to-slate-900 p-8 border border-slate-700/80 shadow-2xl">
        <div className="absolute top-0 right-0 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />
        <div className="relative z-10 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span className="text-xs font-mono tracking-widest text-cyan-400 uppercase font-bold bg-cyan-950/60 px-3 py-1 rounded-full border border-cyan-800">
                AISR MULTIPLAYER • MODULE 6
              </span>
              <div className="flex items-center gap-1.5 text-xs font-mono text-slate-300">
                <span
                  className={`w-2 h-2 rounded-full ${
                    connectionStatus === 'CONNECTED'
                      ? 'bg-emerald-400'
                      : connectionStatus === 'CONNECTING'
                      ? 'bg-amber-400 animate-pulse'
                      : 'bg-red-500'
                  }`}
                />
                <span>{connectionStatus}</span>
              </div>
            </div>
            <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-white">
              Networked Robot Racing Simulator
            </h1>
            <p className="text-slate-400 text-sm mt-1 max-w-2xl">
              Real-time multi-client Pioneer P3-DX race lobby on authoritative FastAPI server with CoppeliaSim + headless mock engine.
            </p>
          </div>
        </div>
      </div>

      {/* Global Error Banner */}
      {errorMessage && (
        <div className="flex items-center gap-3 bg-red-950/80 border border-red-500/80 text-red-200 px-4 py-3 rounded-xl text-sm font-mono shadow-lg">
          <span className="text-lg">⚠</span>
          <span className="flex-1">{errorMessage}</span>
        </div>
      )}

      {/* Primary Actions Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Column: Player Identity & Join by Code */}
        <div className="flex flex-col gap-6">
          {/* Identity Box */}
          <div className="bg-slate-900/80 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-xl">
            <h2 className="text-lg font-bold text-white mb-1 flex items-center gap-2">
              <span className="w-1.5 h-4 bg-cyan-400 rounded-full" />
              Pilot Identity
            </h2>
            <p className="text-xs text-slate-400 mb-4">
              Enter your display name for server-authoritative lobby and robot assignment.
            </p>

            <div className="flex flex-col gap-2">
              <label htmlFor="display-name-input" className="text-xs font-mono text-slate-300">
                Driver Name (1-32 chars)
              </label>
              <input
                id="display-name-input"
                type="text"
                value={displayName}
                onChange={(e) => onDisplayNameChange(e.target.value)}
                maxLength={32}
                placeholder="E.g. ApexPredator, RacerX"
                className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono focus:outline-none focus:ring-2 focus:ring-cyan-400 transition"
              />
              {!isValidDisplayName && displayName.length > 0 && (
                <p className="text-xs text-amber-400 mt-1">
                  Letters, numbers, spaces, and hyphens only (max 32 characters).
                </p>
              )}
            </div>
          </div>

          {/* Join by Room Code Box */}
          <div className="bg-slate-900/80 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-xl flex-1 flex flex-col justify-between">
            <div>
              <h2 className="text-lg font-bold text-white mb-1 flex items-center gap-2">
                <span className="w-1.5 h-4 bg-emerald-400 rounded-full" />
                Join Race Room
              </h2>
              <p className="text-xs text-slate-400 mb-4">
                Enter the 5-character private room code provided by the host.
              </p>

              <form onSubmit={handleJoinSubmit} className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <label htmlFor="room-code-input" className="text-xs font-mono text-slate-300">
                    Room Code (e.g. K9X2P)
                  </label>
                  <input
                    id="room-code-input"
                    type="text"
                    value={roomCodeInput}
                    onChange={(e) => setRoomCodeInput(e.target.value.toUpperCase())}
                    maxLength={16}
                    placeholder="ENTER CODE"
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono text-center tracking-widest text-lg font-bold uppercase focus:outline-none focus:ring-2 focus:ring-emerald-400 transition"
                  />
                </div>

                <div className="flex items-center gap-4">
                  <label className="text-xs font-mono text-slate-300">Join Role:</label>
                  <div className="flex items-center gap-3 text-xs">
                    <label className="flex items-center gap-1.5 text-slate-200 cursor-pointer">
                      <input
                        type="radio"
                        name="joinRole"
                        checked={joinRole === 'driver'}
                        onChange={() => setJoinRole('driver')}
                        className="accent-cyan-400"
                      />
                      Driver (Allocates Robot)
                    </label>
                    <label className="flex items-center gap-1.5 text-slate-200 cursor-pointer">
                      <input
                        type="radio"
                        name="joinRole"
                        checked={joinRole === 'spectator'}
                        onChange={() => setJoinRole('spectator')}
                        className="accent-cyan-400"
                      />
                      Spectator
                    </label>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={!isValidDisplayName || !isValidCode || isBusy || connectionStatus !== 'CONNECTED'}
                  className="w-full mt-2 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-bold py-3 rounded-lg shadow-lg transition disabled:opacity-40 disabled:cursor-not-allowed font-mono uppercase tracking-wider"
                >
                  {isBusy ? 'Joining Room...' : 'Enter Race Room'}
                </button>
              </form>
            </div>
          </div>
        </div>

        {/* Right Column: Create New Room */}
        <div className="bg-slate-900/80 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-xl flex flex-col justify-between">
          <div>
            <h2 className="text-lg font-bold text-white mb-1 flex items-center gap-2">
              <span className="w-1.5 h-4 bg-orange-400 rounded-full" />
              Create Custom Room
            </h2>
            <p className="text-xs text-slate-400 mb-4">
              Host a new room on the server and receive an authoritative 5-char race code.
            </p>

            <form onSubmit={handleCreateSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <label htmlFor="create-room-name-input" className="text-xs font-mono text-slate-300">
                  Room Name
                </label>
                <input
                  id="create-room-name-input"
                  type="text"
                  value={createRoomName}
                  onChange={(e) => setCreateRoomName(e.target.value)}
                  maxLength={64}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono focus:outline-none focus:ring-2 focus:ring-orange-400 transition"
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="create-capacity-select" className="text-xs font-mono text-slate-300">
                  Player Capacity (Grid Limit: 4 Racers)
                </label>
                <select
                  id="create-capacity-select"
                  value={createCapacity}
                  onChange={(e) => setCreateCapacity(Number(e.target.value))}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono focus:outline-none focus:ring-2 focus:ring-orange-400 transition"
                >
                  <option value={2}>2 Racers (1v1 Duel)</option>
                  <option value={3}>3 Racers</option>
                  <option value={4}>4 Racers (Standard Full Grid)</option>
                </select>
              </div>

              <div className="flex items-center gap-4">
                <label className="text-xs font-mono text-slate-300">Host Role:</label>
                <div className="flex items-center gap-3 text-xs">
                  <label className="flex items-center gap-1.5 text-slate-200 cursor-pointer">
                    <input
                      type="radio"
                      name="createRole"
                      checked={createRole === 'driver'}
                      onChange={() => setCreateRole('driver')}
                      className="accent-orange-400"
                    />
                    Driver
                  </label>
                  <label className="flex items-center gap-1.5 text-slate-200 cursor-pointer">
                    <input
                      type="radio"
                      name="createRole"
                      checked={createRole === 'spectator'}
                      onChange={() => setCreateRole('spectator')}
                      className="accent-orange-400"
                    />
                    Spectator
                  </label>
                </div>
              </div>

              <button
                type="submit"
                disabled={!isValidDisplayName || isBusy || connectionStatus !== 'CONNECTED'}
                className="w-full mt-4 bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-400 hover:to-amber-400 text-slate-950 font-bold py-3 rounded-lg shadow-lg transition disabled:opacity-40 disabled:cursor-not-allowed font-mono uppercase tracking-wider"
              >
                {isBusy ? 'Generating Room...' : 'Create & Host Room'}
              </button>
            </form>
          </div>
        </div>
      </div>

      {/* Active Rooms Browser */}
      <div className="bg-slate-900/80 backdrop-blur-md rounded-2xl p-6 border border-slate-700/80 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-white">Active Public Rooms</h2>
            <span className="text-xs font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full">
              {activeRooms.length}
            </span>
          </div>
          <button
            onClick={onRefreshRooms}
            disabled={connectionStatus !== 'CONNECTED'}
            className="text-xs font-mono text-cyan-400 hover:text-cyan-300 flex items-center gap-1.5 transition disabled:opacity-40"
          >
            <span>↻</span> Refresh List
          </button>
        </div>

        {activeRooms.length === 0 ? (
          <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl text-slate-500 text-sm font-mono">
            No active race rooms found on server. Create one above to get started!
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {activeRooms.map((room) => (
              <div
                key={room.room_id}
                className="p-4 rounded-xl border border-slate-800 bg-slate-950/70 hover:border-slate-700 transition flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono text-sm font-bold text-cyan-300">{room.room_code}</span>
                    <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                      {room.player_count} / {room.capacity}
                    </span>
                  </div>
                  <h3 className="text-sm font-semibold text-white truncate">{room.name}</h3>
                  <p className="text-xs text-slate-400 font-mono mt-0.5">Status: {room.status}</p>
                </div>
                <button
                  onClick={() => setRoomCodeInput(room.room_code)}
                  className="mt-3 w-full text-xs font-mono py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 transition text-center"
                >
                  Select Code ({room.room_code})
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
