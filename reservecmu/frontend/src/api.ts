export type Attr = { label: string; reports: number } | null
export interface Space {
  id: number; name: string; formal_name: string; building: string; space_type: string; max_capacity: number
  amenities: string[]; table_type: { value: string | null; source: string | null }
  attributes: { noise: Attr; brightness: Attr; temperature: Attr }
  unverified_preferences: string[]; restricted_to: string | null
  availability: { known: boolean; date: string; free_windows: string[] | null }; url: string
}
export interface Meta {
  space_types: string[]; buildings: string[]; amenities: string[]; affiliations: string[]
  availability_as_of: Record<string, string>; today: string; has_current_data: boolean; demo_mode?: boolean; counts: { spaces: number; with_availability: number }
}
export interface Filters {
  space_types?: string[]; building?: string; min_capacity?: number; amenities?: string[]; table_type?: string
  noise_max?: number; brightness_min?: number; temperature?: string; start_time?: string; end_time?: string
  affiliation?: string; limit: number
}
export interface Recommendation { space: Space; reason: string }
export interface AgentReply { summary: string; recommendations: Recommendation[]; trace?: { filters: Record<string, unknown>; matches?: number; error?: string }[] }

export class ApiError extends Error {
  status: number
  constructor(status: number, msg: string) { super(msg); this.status = status }
}
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({ detail: r.statusText }))).detail ?? r.statusText)
  return r.json()
}

export const getMeta = () => req<Meta>('/meta')

export function searchSpaces(f: Filters) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) continue
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x)); else q.set(k, String(v))
  }
  return req<Space[]>('/spaces?' + q)
}

export const recommend = (query: string, affiliation?: string) =>
  req<AgentReply>('/agent/recommend', { method: 'POST', body: JSON.stringify({ query, affiliation: affiliation || null }) })

export const sendFeedback = (b: { space_id: number; noise?: number; brightness?: number; temperature?: number }) =>
  req<{ ok: boolean }>('/feedback', { method: 'POST', body: JSON.stringify(b) })
