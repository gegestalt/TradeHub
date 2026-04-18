export default async function LobbyPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  return (
    <main className="max-w-4xl mx-auto px-4 py-12">
      <p className="text-zinc-500 text-sm font-mono mb-2">Lobby</p>
      <h1 className="text-3xl font-bold font-mono tracking-tight">
        {code.toUpperCase()}
      </h1>
      <p className="text-zinc-400 mt-4">Competition room — coming soon.</p>
    </main>
  );
}
