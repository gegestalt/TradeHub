import Link from "next/link";
import type { Lobby, LobbyState } from "@/types/lobby";

const STATE_BADGE: Record<LobbyState, { label: string; className: string }> = {
  lobby: { label: "Waiting", className: "text-zinc-400 bg-zinc-800" },
  active: { label: "Live", className: "text-green-400 bg-green-950" },
  ended: { label: "Ended", className: "text-zinc-500 bg-zinc-900" },
};

export default function LobbyCard({ lobby }: { lobby: Lobby }) {
  const badge = STATE_BADGE[lobby.state];

  return (
    <Link
      href={`/${lobby.code}`}
      className="block bg-zinc-900 border border-zinc-800 rounded-lg px-5 py-4 hover:border-zinc-600 hover:bg-zinc-800/60 transition-colors group"
    >
      <div className="flex items-center justify-between">
        <span className="font-medium text-zinc-100 group-hover:text-white">
          {lobby.name}
        </span>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${badge.className}`}
        >
          {badge.label}
        </span>
      </div>
      <div className="mt-1 flex gap-4 text-sm text-zinc-500 font-mono">
        <span>{lobby.code}</span>
        <span>
          {lobby.playerCount} player{lobby.playerCount !== 1 ? "s" : ""}
        </span>
      </div>
    </Link>
  );
}
