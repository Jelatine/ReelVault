import type { Bookmark, Chapter } from '../lib/bookmarks'
import { timecode } from '../editor/frames'

export default function PlayerMarkers({ bookmarks, chapters, duration, seek }: {
  bookmarks: Bookmark[]; chapters: Chapter[]; duration: number; seek: (t: number) => void
}) {
  const marks = bookmarks.filter((m) => !m.stale)
  const all = [...marks.map((m) => ({ id:m.id,position:m.position,title:m.title,note:m.note,kind:m.kind })),
    ...chapters.filter((chapter) => !marks.some((m) => m.position === chapter.start)).map((c) => ({ id:`scene-${c.start}`,position:c.start,title:c.title,note:'',kind:'chapter' }))]
  return <div className="rv-player-markers" role="group" aria-label="进度条书签与章节">
    {all.map((mark) => <button key={mark.id} type="button" className={`rv-player-marker rv-player-marker-${mark.kind}`}
      style={{ left:`${duration > 0 ? mark.position/duration*100 : 0}%` }}
      title={`${timecode(mark.position)} · ${mark.title}${mark.note ? `\n${mark.note}` : ''}`}
      aria-label={`进度条${mark.kind === 'chapter' ? '章节' : '书签'} ${mark.title} ${timecode(mark.position)}`}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation() }}
      onClick={(e) => { e.stopPropagation(); seek(mark.position) }} />)}
  </div>
}
