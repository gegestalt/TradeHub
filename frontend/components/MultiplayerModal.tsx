"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useEffect } from "react";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";

export type ModalView = "choice" | "create" | "join";

interface MultiplayerModalProps {
  view: ModalView;
  onViewChange: (view: ModalView) => void;
  onClose: () => void;
}

const VIEW_TITLE: Record<ModalView, string> = {
  choice: "Play",
  create: "Create Lobby",
  join: "Join Lobby",
};

export default function MultiplayerModal({
  view,
  onViewChange,
  onClose,
}: MultiplayerModalProps) {
  const router = useRouter();
  const [joinCode, setJoinCode] = useState("");
  const [joinPassword, setJoinPassword] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (view === "join") {
      codeInputRef.current?.focus();
    }
  }, [view]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleJoinSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const code = joinCode.trim().toUpperCase();
    const password = joinPassword.trim();

    let valid = true;
    if (code.length !== 6) {
      setCodeError("Lobby codes are 6 characters. Double-check and try again.");
      valid = false;
    } else {
      setCodeError(null);
    }
    if (password.length === 0) {
      setPasswordError("Please enter the lobby password.");
      valid = false;
    } else {
      setPasswordError(null);
    }

    if (valid) {
      router.push(`/${code}`);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-xl w-full max-w-sm p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold">{VIEW_TITLE[view]}</h2>
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-zinc-200 transition-colors text-xl leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {view === "choice" && (
          <div className="flex flex-col gap-3">
            <Button variant="primary" onClick={() => onViewChange("create")}>
              Create Lobby
            </Button>
            <Button variant="secondary" onClick={() => onViewChange("join")}>
              Join Lobby
            </Button>
          </div>
        )}

        {view === "create" && (
          <div className="flex flex-col gap-4">
            <p className="text-zinc-400 text-sm">
              Configure and launch your lobby.
            </p>
            <Button variant="primary" onClick={() => router.push("/create")}>
              Configure Lobby
            </Button>
            <Button variant="ghost" onClick={() => onViewChange("choice")}>
              Back
            </Button>
          </div>
        )}

        {view === "join" && (
          <form onSubmit={handleJoinSubmit} className="flex flex-col gap-4">
            <Input
              ref={codeInputRef}
              label="Lobby Code"
              placeholder="AB12CD"
              value={joinCode}
              onChange={(e) => {
                setJoinCode(e.target.value);
                setCodeError(null);
              }}
              maxLength={6}
              autoComplete="off"
              error={codeError}
              className="font-mono tracking-widest uppercase text-center text-lg"
            />
            <Input
              label="Password"
              type="password"
              placeholder="Enter lobby password"
              value={joinPassword}
              onChange={(e) => {
                setJoinPassword(e.target.value);
                setPasswordError(null);
              }}
              autoComplete="current-password"
              error={passwordError}
            />
            <Button type="submit" variant="primary">
              Join
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onViewChange("choice")}
            >
              Back
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
