import React, { useEffect, useMemo, useState } from 'react';
import './App.css';
import { LobbyView } from './components/LobbyView';
import { RoomView } from './components/RoomView';
import { MultiplayerClient } from './services/MultiplayerClient';
import type { ConnectionStatus, RoomSnapshotMessage, RoomSummaryPayload } from './types/protocol';

export const App: React.FC = () => {
  const client = useMemo(() => new MultiplayerClient(), []);

  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('DISCONNECTED');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentRoom, setCurrentRoom] = useState<RoomSnapshotMessage | null>(null);
  const [assignedRobotId, setAssignedRobotId] = useState<string | null>(null);
  const [activeRooms, setActiveRooms] = useState<RoomSummaryPayload[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState<boolean>(false);

  // Persistent driver display name
  const [displayName, setDisplayName] = useState<string>(() => {
    return localStorage.getItem('aisr_driver_name') || `Pilot_${Math.floor(100 + Math.random() * 900)}`;
  });

  const handleDisplayNameChange = (name: string) => {
    setDisplayName(name);
    localStorage.setItem('aisr_driver_name', name);
  };

  useEffect(() => {
    client.onStatusChange = (status) => {
      setConnectionStatus(status);
      if (status === 'CONNECTED') {
        setErrorMessage(null);
        client.listRooms();
      }
    };

    client.onWelcome = (msg) => {
      setSessionId(msg.session_id);
      client.listRooms();
    };

    client.onRoomSnapshot = (snapshot) => {
      setCurrentRoom(snapshot);
      setIsBusy(false);
      setErrorMessage(null);
    };

    client.onRobotAssigned = (msg) => {
      setAssignedRobotId(msg.robot_id);
    };

    client.onRobotUnavailable = (msg) => {
      if (client.sessionId === msg.session_id) {
        setAssignedRobotId(null);
        setErrorMessage(`Assigned robot '${msg.robot_id}' became unavailable: ${msg.reason || 'Hardware fault'}`);
      }
    };

    client.onPlayerLeft = (msg) => {
      if (msg.session_id === client.sessionId) {
        setCurrentRoom(null);
        setAssignedRobotId(null);
        client.listRooms();
      }
    };

    client.onRoomList = (msg) => {
      setActiveRooms(msg.rooms || []);
    };

    client.onError = (err) => {
      setErrorMessage(`[${err.code}] ${err.message}`);
      setIsBusy(false);
    };

    // Auto-connect to backend on mount
    client.connect().catch((err) => {
      console.warn('Initial WebSocket connection error:', err);
    });

    return () => {
      client.disconnect();
    };
  }, [client]);

  const handleCreateRoom = (name: string, capacity: number, role: 'driver' | 'spectator') => {
    setIsBusy(true);
    setErrorMessage(null);
    const ok = client.createRoom(name, capacity, role, displayName);
    if (!ok) {
      setIsBusy(false);
      setErrorMessage('Failed to send create_room command. Check connection.');
    }
  };

  const handleJoinRoom = (code: string, role: 'driver' | 'spectator') => {
    setIsBusy(true);
    setErrorMessage(null);
    const ok = client.joinRoom(code, displayName, role);
    if (!ok) {
      setIsBusy(false);
      setErrorMessage('Failed to send join_room command. Check connection.');
    }
  };

  const handleLeaveRoom = () => {
    client.leaveRoom();
    setCurrentRoom(null);
    setAssignedRobotId(null);
    client.listRooms();
  };

  const handleToggleReady = (nextReady: boolean) => {
    client.setReady(nextReady);
  };

  const handleRefreshRooms = () => {
    client.listRooms();
  };

  const handleReconnect = () => {
    client.connect();
  };

  return (
    <div className="app-container font-sans bg-slate-950 text-slate-100 min-h-screen flex flex-col justify-between">
      {/* Top Navbar */}
      <header className="app-header py-3 px-6 border-b border-slate-800 bg-slate-900/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 to-blue-600 flex items-center justify-center font-black text-slate-950 text-base shadow-lg shadow-cyan-500/20">
              🏎
            </span>
            <div className="flex flex-col">
              <span className="font-extrabold tracking-tight text-white text-base leading-none">
                AISR MULTIPLAYER
              </span>
              <span className="text-[10px] font-mono text-cyan-400 tracking-wider uppercase mt-0.5">
                Robot Racing Simulator
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {sessionId && (
              <div className="hidden sm:flex items-center gap-1.5 px-3 py-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-300">
                <span className="text-slate-500">ID:</span>
                <span className="text-cyan-300 font-bold">{sessionId}</span>
              </div>
            )}

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 px-3 py-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono">
                <span
                  className={`w-2 h-2 rounded-full ${
                    connectionStatus === 'CONNECTED'
                      ? 'bg-emerald-400'
                      : connectionStatus === 'CONNECTING'
                      ? 'bg-amber-400 animate-pulse'
                      : 'bg-red-500'
                  }`}
                />
                <span className="text-slate-300">{connectionStatus}</span>
              </div>

              {connectionStatus === 'DISCONNECTED' && (
                <button
                  onClick={handleReconnect}
                  className="px-3 py-1 bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs rounded-lg font-mono transition"
                >
                  Reconnect
                </button>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="app-main flex-1 max-w-6xl w-full mx-auto p-4 md:p-6 my-auto flex flex-col justify-center">
        {currentRoom ? (
          <RoomView
            room={currentRoom}
            sessionId={sessionId || ''}
            assignedRobotId={assignedRobotId}
            onToggleReady={handleToggleReady}
            onLeaveRoom={handleLeaveRoom}
            errorMessage={errorMessage}
          />
        ) : (
          <LobbyView
            connectionStatus={connectionStatus}
            displayName={displayName}
            onDisplayNameChange={handleDisplayNameChange}
            onCreateRoom={handleCreateRoom}
            onJoinRoom={handleJoinRoom}
            onRefreshRooms={handleRefreshRooms}
            activeRooms={activeRooms}
            errorMessage={errorMessage}
            isBusy={isBusy}
          />
        )}
      </main>

      {/* Bottom Footer */}
      <footer className="app-footer border-t border-slate-800/80 bg-slate-950 py-3 px-6 text-center text-xs font-mono text-slate-500">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2">
          <span>AISR Robotics Lab • Advanced Multi-Robot Racing Simulator</span>
          <span className="text-slate-600">Protocol v1 • Three.js WebGL Engine • FastAPI Backend</span>
        </div>
      </footer>
    </div>
  );
};

export default App;
