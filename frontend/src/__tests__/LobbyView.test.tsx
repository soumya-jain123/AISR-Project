import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LobbyView } from '../components/LobbyView';

describe('LobbyView Component', () => {
  it('renders pilot profile and lobby form inputs', () => {
    const handleDisplayNameChange = vi.fn();
    const handleCreateRoom = vi.fn();
    const handleJoinRoom = vi.fn();
    const handleRefresh = vi.fn();

    render(
      <LobbyView
        connectionStatus="CONNECTED"
        displayName="RacerOne"
        onDisplayNameChange={handleDisplayNameChange}
        onCreateRoom={handleCreateRoom}
        onJoinRoom={handleJoinRoom}
        onRefreshRooms={handleRefresh}
        activeRooms={[]}
      />
    );

    expect(screen.getByText('Networked Robot Racing Simulator')).toBeInTheDocument();
    expect(screen.getByDisplayValue('RacerOne')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create & host room/i })).toBeInTheDocument();
  });

  it('triggers create room with selected options', () => {
    const handleCreateRoom = vi.fn();

    render(
      <LobbyView
        connectionStatus="CONNECTED"
        displayName="RacerOne"
        onDisplayNameChange={vi.fn()}
        onCreateRoom={handleCreateRoom}
        onJoinRoom={vi.fn()}
        onRefreshRooms={vi.fn()}
        activeRooms={[]}
      />
    );

    const createBtn = screen.getByRole('button', { name: /create & host room/i });
    fireEvent.click(createBtn);

    expect(handleCreateRoom).toHaveBeenCalledWith('Pioneer Circuit 1', 4, 'driver');
  });

  it('triggers join room with entered uppercase code', () => {
    const handleJoinRoom = vi.fn();

    render(
      <LobbyView
        connectionStatus="CONNECTED"
        displayName="RacerOne"
        onDisplayNameChange={vi.fn()}
        onCreateRoom={vi.fn()}
        onJoinRoom={handleJoinRoom}
        onRefreshRooms={vi.fn()}
        activeRooms={[]}
      />
    );

    const codeInput = screen.getByPlaceholderText('ENTER CODE');
    fireEvent.change(codeInput, { target: { value: 'k9x2p' } });

    const joinBtn = screen.getByRole('button', { name: /enter race room/i });
    fireEvent.click(joinBtn);

    expect(handleJoinRoom).toHaveBeenCalledWith('K9X2P', 'driver');
  });

  it('disables actions when disconnected', () => {
    render(
      <LobbyView
        connectionStatus="DISCONNECTED"
        displayName="RacerOne"
        onDisplayNameChange={vi.fn()}
        onCreateRoom={vi.fn()}
        onJoinRoom={vi.fn()}
        onRefreshRooms={vi.fn()}
        activeRooms={[]}
      />
    );

    expect(screen.getByRole('button', { name: /create & host room/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /enter race room/i })).toBeDisabled();
  });
});
