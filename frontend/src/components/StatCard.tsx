import type { StatCardData } from "@/types"
import { cn } from "@/lib/utils"
import { Star } from "lucide-react"

interface Props {
  stat: StatCardData
  pinned: boolean
  onTogglePin?: (id: string) => void
  compact?: boolean
}

export function StatCard({ stat, pinned, onTogglePin, compact }: Props) {
  if (compact) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1 shadow-xs">
        <span className="text-[11px] text-muted-foreground font-medium">{stat.label}</span>
        <span className="text-xs font-semibold tabular-nums truncate max-w-32">{stat.value}</span>
      </div>
    )
  }

  return (
    <div
      className={cn(
        "group relative rounded-lg border bg-card p-3 text-center transition-shadow hover:shadow-sm",
        onTogglePin && "cursor-pointer",
      )}
      onClick={() => onTogglePin?.(stat.id)}
      role={onTogglePin ? "button" : undefined}
      tabIndex={onTogglePin ? 0 : undefined}
      onKeyDown={(e) => {
        if (onTogglePin && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault()
          onTogglePin(stat.id)
        }
      }}
    >
      <Star
        className={cn(
          "absolute top-1.5 right-1.5 size-3.5 transition-colors pointer-events-none",
          pinned
            ? "text-favorite fill-favorite"
            : "text-muted-foreground/0 group-hover:text-muted-foreground/50",
        )}
      />
      <p className="text-lg font-bold tabular-nums leading-tight truncate">{stat.value}</p>
      <p className="text-xs text-muted-foreground mt-0.5 truncate">{stat.label}</p>
      {stat.subtitle && (
        <p className="text-[10px] text-muted-foreground/70 mt-0.5 leading-snug">{stat.subtitle}</p>
      )}
    </div>
  )
}
