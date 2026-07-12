export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold tracking-tight">Kairos</h1>
          <p className="text-sm text-zinc-500">
            Ship AI pipelines with confidence
          </p>
        </div>
        {children}
      </div>
    </main>
  );
}
