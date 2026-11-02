import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MultiplayerClient } from '../services/MultiplayerClient';
import { PROTOCOL_VERSION } from '../types/protocol';
import type { RoomBroadcastMessage, SafetyEventMessage } from '../types/protocol';

describe('MultiplayerClient Driving & Safety Controls (Module 7)', () => {
  let client: MultiplayerClient;
  let mockSend: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    client = new MultiplayerClient('ws://127.0.0.1:8000/ws/multiplayer');
    client.sessionId = 'sess_driver_42';
    client.status = 'CONNECTED';
    client.assignedRobotId = 'robot_1';
    client.currentRoom = {
      v: PROTOCOL_VERSION,
      type: 'room_snapshot',
      seq: 1,
      ts: 1000,
      room_id: 'room_race_01',
      room_code: 'RACE1',
      name: 'Grand Prix',
      owner_id: 'sess_driver_42',
      capacity: 4,
      status: 'WAITING',
      members: [],
    };

    mockSend = vi.fn();
    (client as any).ws = {
      readyState: WebSocket.OPEN,
      send: mockSend,
      close: vi.fn(),
    };
  });

  it('clamps throttle and steering within [-1.0, 1.0] and dispatches player_input', () => {
    const ok = client.sendInput(2.5, -3.0, 'active');
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.v).toBe(PROTOCOL_VERSION);
    expect(sent.type).toBe('player_input');
    expect(sent.session_id).toBe('sess_driver_42');
    expect(sent.throttle).toBe(1.0);
    expect(sent.steering).toBe(-1.0);
    expect(sent.control_state).toBe('active');
    expect(sent.seq).toBeGreaterThan(0);
  });

  it('rate-limits continuous non-neutral driving inputs', () => {
    // First call goes through
    const firstOk = client.sendInput(0.8, 0.2, 'active');
    expect(firstOk).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    // Immediate second call is suppressed by client rate limiter
    const secondOk = client.sendInput(0.8, 0.2, 'active');
    expect(secondOk).toBe(false);
    expect(mockSend).toHaveBeenCalledTimes(1);

    // Neutral inputs bypass rate limiter to ensure safety release
    const neutralOk = client.sendInput(0, 0, 'released');
    expect(neutralOk).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(2);
  });

  it('sends individual_stop when sendStop is invoked', () => {
    const ok = client.sendStop('Brake pedal pressed');
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('individual_stop');
    expect(sent.session_id).toBe('sess_driver_42');
    expect(sent.reason).toBe('Brake pedal pressed');
  });

  it('sends individual_estop when sendEstop is invoked', () => {
    const ok = client.sendEstop('Manual E-stop pressed');
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('individual_estop');
    expect(sent.session_id).toBe('sess_driver_42');
    expect(sent.reason).toBe('Manual E-stop pressed');
  });

  it('sends room_estop with room_id when sendRoomEstop is invoked', () => {
    const ok = client.sendRoomEstop('Host red flag');
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('room_estop');
    expect(sent.session_id).toBe('sess_driver_42');
    expect(sent.room_id).toBe('room_race_01');
    expect(sent.reason).toBe('Host red flag');
  });

  it('sends room_reset with room_id when sendRoomReset is invoked', () => {
    const ok = client.sendRoomReset();
    expect(ok).toBe(true);
    expect(mockSend).toHaveBeenCalledTimes(1);

    const sent = JSON.parse(mockSend.mock.calls[0][0]);
    expect(sent.type).toBe('room_reset');
    expect(sent.session_id).toBe('sess_driver_42');
    expect(sent.room_id).toBe('room_race_01');
  });

  it('invokes onRoomBroadcast listener when room_broadcast message arrives', () => {
    const broadcastSpy = vi.fn();
    client.onRoomBroadcast = broadcastSpy;

    const broadcastMsg: RoomBroadcastMessage = {
      v: PROTOCOL_VERSION,
      type: 'room_broadcast',
      seq: 10,
      ts: Date.now() / 1000,
      room_id: 'room_race_01',
      race_state: 'WAITING',
      robots: [
        {
          robot_id: 'robot_1',
          player_id: 'sess_driver_42',
          x: 1.25,
          y: -0.5,
          theta: 0.15,
          linear_vel: 0.8,
          angular_vel: 0.1,
          state: 'MOVING',
          lap: 0,
          checkpoint: 0,
        },
      ],
    };

    (client as any).handleServerMessage(broadcastMsg);
    expect(broadcastSpy).toHaveBeenCalledTimes(1);
    expect(broadcastSpy).toHaveBeenCalledWith(broadcastMsg);
  });

  it('invokes onSafetyEvent listener when safety_event message arrives', () => {
    const safetySpy = vi.fn();
    client.onSafetyEvent = safetySpy;

    const safetyMsg: SafetyEventMessage = {
      v: PROTOCOL_VERSION,
      type: 'safety_event',
      seq: 11,
      ts: Date.now() / 1000,
      scope: 'individual',
      event: 'INDIVIDUAL_ESTOP',
      target_id: 'robot_1',
      reason: 'Driver hit E-Stop',
      state: 'ESTOP_LATCHED',
    };

    (client as any).handleServerMessage(safetyMsg);
    expect(safetySpy).toHaveBeenCalledTimes(1);
    expect(safetySpy).toHaveBeenCalledWith(safetyMsg);
  });
});
