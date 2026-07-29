import { useState } from "react"
import type { Favorite } from "@/types"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Separator } from "@/components/ui/separator"

interface Props {
  favorites: Favorite[]
  act: (fn: () => Promise<unknown>) => void
}

export function FavouritesSection({ favorites, act }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set())

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function selectAll() {
    setSelected((prev) =>
      prev.size === favorites.length ? new Set() : new Set(favorites.map((f) => f.track_id))
    )
  }

  async function remove() {
    const ids = [...selected]
    await act(() =>
      fetch("/api/favorites/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_ids: ids }),
      }).then((r) => { if (!r.ok) throw new Error("Failed") })
    )
    setSelected(new Set())
  }

  return (
    <Card className="space-y-3 p-4" id="favourites">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">Favourites</h3>
        <Button
          variant="outline"
          size="sm"
          disabled={selected.size === 0}
          onClick={remove}
        >
          Remove selected
        </Button>
      </div>

      <Separator />

      {favorites.length === 0 ? (
        <p className="text-sm text-muted-foreground py-4 text-center">
          Nothing has crossed the threshold yet. Keep listening.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={selected.size === favorites.length && favorites.length > 0}
                    onCheckedChange={selectAll}
                  />
                </TableHead>
                <TableHead>Track</TableHead>
                <TableHead>Artist</TableHead>
                <TableHead className="text-right">Counted</TableHead>
                <TableHead className="text-right">Played</TableHead>
                <TableHead>In playlist</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {favorites.map((f) => (
                <TableRow key={f.track_id}>
                  <TableCell>
                    <Checkbox
                      checked={selected.has(f.track_id)}
                      onCheckedChange={() => toggle(f.track_id)}
                    />
                  </TableCell>
                  <TableCell className="font-medium">{f.name}</TableCell>
                  <TableCell className="text-muted-foreground">{f.artist}</TableCell>
                  <TableCell className="text-right tabular-nums">{f.qualified_plays}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {f.total_plays}
                  </TableCell>
                  <TableCell>
                    {f.in_playlist ? (
                      <span className="text-xs text-emerald-500">yes</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">no</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        <strong>Counted</strong> is plays where you heard enough of the track.
        Only counted plays move a song towards the playlist.
      </p>
    </Card>
  )
}
