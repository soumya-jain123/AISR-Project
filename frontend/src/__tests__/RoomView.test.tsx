import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { RoomView } from '../components/RoomView';
import type { RoomSnapshotMessage } from '../types/protocol';

describe('RoomView Component', () => {
  const mockRoom: RoomSnapshotMessage = {
    v: 1,
    type: 'room_snapshot',
    seq: 10,
    ts: 1730000000.0,
    room_id: 'room_abc123',
    room_code: 'K9X2P',
    name: 'Grand Prix Circuit',
    owner_id: 'sess_1',
    capacity: 4,
    status: 'WAITING',
    members: [
      {
        session_id: 'sess_1',
        display_name: 'Host Pilot',
        role: 'driver',
        robot_id: 'robot_1',
        is_ready: true,
        is_owner: true,
        joined_at: 1730000000.0,
      },
      {
        session_id: 'sess_2',
        display_name: 'Guest Racer',
        role: 'driver',
        robot_id: 'robot_2',
        is_ready: false,
        is_owner: false,
        joined_at: 1730000005.0,
      },
    ],
  };

  it('renders room code, name, and members roster', () => {
    render(
      <RoomView
        room={mockRoom}
        sessionId="sess_2"
        assignedRobotId="robot_2"
        onToggleReady={vi.fn()}
        onLeaveRoom={vi.fn()}
      />
    );

    expect(screen.getByText('Grand Prix Circuit')).toBeInTheDocument();
    expect(screen.getByText('K9X2P')).toBeInTheDocument();
    expect(screen.getByText('Host Pilot')).toBeInTheDocument();
    expect(screen.getByText('Guest Racer')).toBeInTheDocument();
    expect(screen.getByText('• robot_2')).toBeInTheDocument();
  });

  it('handles toggle ready interaction', () => {
    const handleToggleReady = vi.fn();

    render(
      <RoomView
        room={mockRoom}
        sessionId="sess_2"
        assignedRobotId="robot_2"
        onToggleReady={handleToggleReady}
        onLeaveRoom={vi.fn()}
      />
    );

    const readyBtn = screen.getByRole('button', { name: /ready up for race/i });
    fireEvent.click(readyBtn);

    expect(handleToggleReady).toHaveBeenCalledWith(true);
  });

  it('handles leave room interaction', () => {
    const handleLeave = vi.fn();

    render(
      <RoomView
        room={mockRoom}
        sessionId="sess_2"
        assignedRobotId="robot_2"
        onToggleReady={vi.fn()}
        onLeaveRoom={handleLeave}
      />
    );

    const leaveBtn = screen.getByRole('button', { name: /leave race room/i });
    fireEvent.click(leaveBtn);

    expect(handleLeave).toHaveBeenCalledTimes(1);
  });

  it('displays structured error messages when present', () => {
    render(
      <RoomView
        room={mockRoom}
        sessionId="sess_2"
        assignedRobotId="robot_2"
        onToggleReady={vi.fn()}
        onLeaveRoom={vi.fn()}
        errorMessage="[ERR_ROBOT_UNAVAILABLE] Robot hardware faulted"
      />
    );

    expect(
      screen.getByText('[ERR_ROBOT_UNAVAILABLE] Robot hardware faulted')
    ).toBeInTheDocument();
  });
});
