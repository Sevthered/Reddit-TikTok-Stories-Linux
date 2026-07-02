export type AgentStatus = {
	label: string;
	loaded: boolean;
	pid: number | null;
	last_exit_code: number | null;
};

export type StatusOut = {
	posts_today: number;
	posts_per_day_cap: number;
	uploads_enabled: boolean;
	last_uploaded_at: string | null;
	sessionid_days_remaining: number | null;
	post_tz: string;
	madrid_offset_hours: number;
	now_iso: string;
	agents: AgentStatus[];
	pending_count: number;
	under_review_count: number;
};

export type HealthOut = {
	ok: boolean;
	db_reachable: boolean;
	db_path: string;
	config_loaded: boolean;
	config_path: string;
	config_error: string | null;
	env_path_exists: boolean;
	cookies_exists: boolean;
	logs_dir: string;
	dev_mode: boolean;
	post_tz: string;
	madrid_offset_hours: number;
};

export type CookieHealthOut = {
	sessionid_days_remaining: number | null;
};

export type RenderOut = {
	post_id: string;
	title: string;
	subreddit: string;
	author: string;
	caption: string;
	video_path: string;
	cover_path: string;
	upload_status: string;
	upload_attempts: number;
	next_retry_at: string | null;
	telegram_msg_id: number | null;
	uploaded_at: string | null;
	tiktok_url: string | null;
};

export async function apiGet<T>(path: string): Promise<T> {
	const r = await fetch(path);
	if (!r.ok) throw new Error(`${path} → ${r.status}`);
	return (await r.json()) as T;
}

export type TomlOut = {
	path: string;
	content: string;
};

export type EnvEntry = {
	key: string;
	value_masked: string;
	is_secret: boolean;
};

export type EnvOut = {
	path: string;
	entries: EnvEntry[];
};

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
	const r = await fetch(path, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!r.ok) {
		let msg = `${r.status}`;
		try {
			const j = await r.json();
			if (j?.detail) msg = j.detail;
		} catch {
			// non-JSON body — keep status code
		}
		throw new Error(msg);
	}
	return (await r.json()) as T;
}

export type JobOut = {
	id: string;
	kind: 'render' | 'upload' | 'confirm';
	args: string[];
	started_at: string;
	ended_at: string | null;
	exit_code: number | null;
	running: boolean;
	line_count: number;
};

// ---- Schedule tab (mirrors webapp/backend/routers/schedule.py) --------

export type SlotOverrideValue = string | boolean | null;

export type SlotEffective = {
	instance: string;
	publish_hour: number;
	render_time: string;
	upload_time: string;
	render_enabled: boolean;
	upload_enabled: boolean;
	auto_approve: boolean;
	notify_render_pre: boolean;
	notify_render_crash: boolean;
	notify_render_empty: boolean;
	notify_upload_approval_card: boolean;
	notify_upload_force_approve: boolean;
	notify_upload_success: boolean;
	notify_upload_failure: boolean;
	notify_upload_gate_reject: boolean;
};

export type SlotView = {
	instance: string;
	defaults: Record<string, string | boolean | number>;
	overrides: Record<string, string>;
	effective: SlotEffective;
};

export type SlotsOut = {
	slots: SlotView[];
	helper_available: boolean;
};

export type OverrideIn = {
	overrides: Record<string, SlotOverrideValue>;
};

export type PutSlotResult = {
	slot: SlotView;
	applied_time_changes: string[];
	warnings: string[];
};

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
	const r = await fetch(path, {
		method: 'POST',
		headers: body ? { 'Content-Type': 'application/json' } : {},
		body: body ? JSON.stringify(body) : undefined
	});
	if (!r.ok) {
		let msg = `${r.status}`;
		try {
			const j = await r.json();
			if (j?.detail) msg = j.detail;
		} catch {
			// non-JSON body — keep status code
		}
		throw new Error(msg);
	}
	return (await r.json()) as T;
}
