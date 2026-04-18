"use client";

import { useState } from "react";
import { MOCK_LOBBIES } from "@/data/mockLobbies";
import LobbyList from "@/components/LobbyList";
import MultiplayerModal, {
  type ModalView,
} from "@/components/MultiplayerModal";

type ModalState = null | ModalView;

export default function HomePage() {
  const [modalView, setModalView] = useState<ModalState>(null);

  return (
    <main className="max-w-2xl mx-auto px-4 py-12">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">TradeHub</h1>
        <p className="text-zinc-400 mt-1">
          Join a competition or create one for your group.
        </p>
      </header>

      <LobbyList lobbies={MOCK_LOBBIES} />

      <button
        onClick={() => setModalView("choice")}
        aria-label="Open lobby menu"
        className="fixed bottom-8 right-8 w-14 h-14 rounded-full bg-green-500 hover:bg-green-400 text-black text-2xl font-bold shadow-lg transition-colors flex items-center justify-center"
      >
        +
      </button>

      {modalView !== null && (
        <MultiplayerModal
          view={modalView}
          onViewChange={(v) => setModalView(v)}
          onClose={() => setModalView(null)}
        />
      )}
    </main>
  );
}
