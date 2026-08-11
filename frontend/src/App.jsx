import { Button } from "@/components/ui/button"

export function App() {
  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <section className="flex max-w-md min-w-0 flex-col gap-4 text-sm leading-loose">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            CA document processing
          </p>
          <h1 className="mt-1 text-2xl font-semibold">CAssist</h1>
          <p className="mt-2 text-muted-foreground">
            Upload, extract, review, and export accounting documents.
          </p>
          <Button className="mt-4">Upload a document</Button>
        </div>
        <div className="font-mono text-xs text-muted-foreground">
          (Press <kbd>d</kbd> to toggle dark mode)
        </div>
      </section>
    </main>
  )
}

export default App
