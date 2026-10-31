import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MultiplayerClient } from '../services/MultiplayerClient';
import { PROTOCOL_VERSION } from '../types/protocol';

describe('MultiplayerClient Protocol & Validation', () => {
  let client: MultiplayerClient;
  let mockSend: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    client = new MultiplayerClient('ws://127.0.0.1:8000/ws/multiplayer');
    client.sessionId = 'sess_test_123';
    client.status = 'CONNECTED';

    // Mock underlying WebSocket
    mockSend = vi.fn();
    (client as any).ws = {
      readyState: WebSocket.OPEN,
      send: mockSend,
      close: vi.fn(),
    };
  });

  it('generates strictly valid create_room message with monotonic sequence', () => {
    const ok = client.createRoom('Circuit Alpha', 4, 'driver', 'Racer One');
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.v).toBe(PROTOCOL_VERSION);
    expect(sent.type).toBe('create_room');
    expect(sent.seq).toBeGreaterThan(0);
    expect(sent.ts).toBeGreaterThan(0);
    expect(sent.session_id).toBe('sess_test_123');
    expect(sent.name).toBe('Circuit Alpha');
    expect(sent.capacity).toBe(4);
    expect(sent.role).toBe('driver');
    expect(sent.display_name).toBe('Racer One');
  });

  it('generates strictly valid join_room message with uppercase code', () => {
    const ok = client.joinRoom('k9x2p', 'Racer Two', 'driver');
    expect(ok).toBe(true);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.v).toBe(PROTOCOL_VERSION);
    expect(sent.type).toBe('join_room');
    expect(sent.room_id).toBe('K9X2P');
    expect(sent.display_name).toBe('Racer Two');
    expect(sent.role).toBe('driver');
  });

  it('generates strictly valid set_ready message', () => {
    client.currentRoom = {
      v: 1,
      type: 'room_snapshot',
      seq: 1,
      ts: 1000,
      room_id: 'room_abc',
      room_code: 'ABC12',
      name: 'Test Room',
      owner_id: 'sess_test_123',
      capacity: 4,
      status: 'WAITING',
      members: [],
    };

    const ok = client.setReady(true);
    expect(ok).toBe(true);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('set_ready');
    expect(sent.is_ready).toBe(true);
    expect(sent.room_id).toBe('room_abc');
  });

  it('generates strictly valid leave_room message', () => {
    client.currentRoom = {
      v: 1,
      type: 'room_snapshot',
      seq: 1,
      ts: 1000,
      room_id: 'room_abc',
      room_code: 'ABC12',
      name: 'Test Room',
      owner_id: 'sess_test_123',
      capacity: 4,
      status: 'WAITING',
      members: [],
    };

    const ok = client.leaveRoom();
    expect(ok).toBe(true);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('leave_room');
    expect(sent.room_id).toBe('room_abc');
    expect(client.currentRoom).toBeNull();
  });

  it('never emits player_input or motion control commands in Module 6', () => {
    // Assert no motion dispatch methods exist on client in Module 6
    expect((client as any).sendControlInput).toBeUndefined();
    expect((client as any).applyThrottleSteering).toBeUndefined();
    expect(mockSend).not.toHaveBeenCalledWith(expect.stringContaining('player_input'));
  });
});
