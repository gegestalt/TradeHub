import type { Lobby } from "@/types/lobby";
import LobbyCard from "./LobbyCard";

export default function LobbyList({ lobbies }: { lobbies: Lobby[] }) {
  if (lobbies.length === 0) {
    return (
      <p className="text-zinc-500 text-sm py-8 text-center">
        No lobbies yet. Hit + to create one.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-3" role="list">
      {lobbies.map((lobby) => (
        <li key={lobby.code}>
          <LobbyCard lobby={lobby} />
        </li>
      ))}
    </ul>
  );
}
