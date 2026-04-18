import type { Lobby, CreateLobbyParams } from "@/types/lobby";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchLobbies(): Promise<Lobby[]> {
  // TODO: GET /competitions
  throw new Error("fetchLobbies not implemented — use MOCK_LOBBIES for now");
}

export async function fetchLobby(code: string): Promise<Lobby> {
  const res = await fetch(`${API_BASE}/competitions/${code}`);
  if (!res.ok) throw new Error(`Lobby ${code} not found`);
  return res.json() as Promise<Lobby>;
}

export async function createLobby(
  params: CreateLobbyParams
): Promise<{ code: string }> {
  // TODO: POST /competitions
  void params;
  throw new Error("createLobby not implemented");
}

export async function joinLobby(
  code: string,
  displayName: string,
  password: string
): Promise<{ playerToken: string }> {
  // TODO: POST /competitions/{code}/join
  void code;
  void displayName;
  void password;
  throw new Error("joinLobby not implemented");
}
