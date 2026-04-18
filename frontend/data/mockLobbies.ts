import type { Lobby } from "@/types/lobby";

export const MOCK_LOBBIES: Lobby[] = [
  {
    code: "AB12CD",
    name: "Friday Night Stocks",
    playerCount: 3,
    maxPlayers: null,
    state: "lobby",
    createdAt: "2026-04-18T18:00:00Z",
  },
  {
    code: "XY99ZZ",
    name: "Crypto Chaos",
    playerCount: 5,
    maxPlayers: null,
    state: "active",
    createdAt: "2026-04-17T10:00:00Z",
  },
  {
    code: "QQ34AB",
    name: "Weekend Warriors",
    playerCount: 2,
    maxPlayers: null,
    state: "ended",
    createdAt: "2026-04-15T09:00:00Z",
  },
];
