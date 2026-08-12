import * as React from "react"
import { RiWifiOffLine } from "@remixicon/react"

export function ConnectionStatus() {
  const [online, setOnline] = React.useState(() => navigator.onLine)

  React.useEffect(() => {
    const handleOnline = () => setOnline(true)
    const handleOffline = () => setOnline(false)
    window.addEventListener("online", handleOnline)
    window.addEventListener("offline", handleOffline)
    return () => {
      window.removeEventListener("online", handleOnline)
      window.removeEventListener("offline", handleOffline)
    }
  }, [])

  if (online) return null
  return (
    <div
      className="fixed right-4 bottom-4 z-50 flex items-center gap-2 rounded-lg border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-lg"
      role="status"
    >
      <RiWifiOffLine className="size-4 text-destructive" />
      You’re offline. Reconnect to continue.
    </div>
  )
}
