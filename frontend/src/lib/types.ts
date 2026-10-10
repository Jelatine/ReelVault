export interface Video {
  storage_id?: string
  metadata?: {
    device_make: string | null
    device_model: string | null
    gps: { latitude: number; longitude: number; altitude: number | null } | null
    custom_fields: Record<string, string>
    overridden: string[]
  }
  id: string
  title: string
  captured_at: string | null
  search_excerpt?: string
  search_pinyin?: boolean
  rating: number
  favorite: boolean
  description: string
  source_video_id?: string | null
  edit_params?: Record<string, unknown> | null
  edited_at?: string | null
  original_name: string
  folder_id: number | null
  status: 'processing' | 'ready' | 'error'
  error: string | null
  size: number
  duration: number
  width: number
  height: number
  fps: number
  bitrate: number
  container: string
  video_codec: string
  audio_codec: string | null
  rotation: number
  cover_time: number | null
  tags: string[]
  created_at: string
  updated_at: string | null
  deleted_at: string | null
  stream_url: string
  download_url: string
  poster_url: string | null
  preview_url: string | null
  thumbnails_url: string | null
}

export interface VideoPage {
  search_terms?: string[]
  items: Video[]
  total: number
  page: number
  page_size: number
}

export interface Folder {
  id: number
  name: string
  parent_id: number | null
  count: number
}

export interface Tag {
  id: number
  name: string
  count: number
  trash_count: number
  color: string | null
  group_id: number | null
  group_name: string | null
}

export type JobStatus = 'queued' | 'running' | 'paused' | 'succeeded' | 'failed' | 'canceled'

export interface Job {
  id: string
  kind: 'ingest' | 'edit' | 'scenes' | 'hls' | 'duplicates' | 'link_import' | 'playable' | 'original_cache' | 'transcribe' | 'vision_index' | 'ai_analyze'
  status: JobStatus
  priority?: number
  eta_seconds?: number | null
  retry_of?: string | null
  conflicting_jobs?: string[]
  params: { edit?: { op: string; [k: string]: unknown }; output?: { mode: string }; name?: string
    url?: string; title?: string; imported_title?: string; storage_id?: string
    encoding?: { requested: string; encoder: string; decoder?: string | null; fallback: string | null }
    summary?: { total: number; scanned: number; cached: number; visual_failed: number; similar_pairs: number; errors: { video_id: string; error: string }[] } }
  video_ids: string[]
  result_video_id: string | null
  has_result_file: boolean
  progress: number
  message: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface DeviceSession {
  id: string
  device_name: string
  user_agent: string
  ip: string
  remember: boolean
  created_at: string
  last_seen_at: string
  expires_at: string
  current: boolean
}

export interface SystemInfo {
  version: string
  ffmpeg_version: string
  workers: number
  running_jobs: number
  queued_jobs: number
  paused_jobs?: number
  disk: { total: number; used: number; free: number }
  library: { count: number; size: number }
  trash: { count: number; size: number }
  trash_retention_days: number
  import_dir: string | null
}

export interface UpdateStatus {
  current_version: string
  latest_version: string | null
  update_available: boolean
  release: {
    tag: string
    name: string
    url: string
    notes: string
    published_at: string | null
    prerelease: boolean
  } | null
  checked_at: string | null
  check_error: string | null
  check_enabled: boolean
  repo: string
  install_mode: 'package' | 'docker' | 'source' | 'none'
  can_auto_upgrade: boolean
  auto_upgrade_blocker: string | null
  systemd_sync_enabled?: boolean
  instructions: string
  phase: 'idle' | 'downloading' | 'verifying' | 'installing' | 'restarting' | 'failed'
  message: string
  error: string | null
}
