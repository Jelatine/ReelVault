export interface ShareLink {
  id: string
  title: string
  video_id: string | null
  collection_id: number | null
  url: string
  expires_at: string
  created_at: string
  password_required: boolean
  allow_download: boolean
  expired: boolean
}
export interface ShareTarget { video_id?: string; collection_id?: number }
export interface SharedVideo {
  id: string; title: string; duration: number; width: number; height: number
  playback_ready?: boolean; stream_url: string; poster_url: string | null; download_url: string | null
}
export interface PublicShare { title: string; expires_at: string; allow_download: boolean; items: SharedVideo[] }
